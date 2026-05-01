import io
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.db.models import Case, IntegerField, Max, Value, When
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from Attenova.api_utils import parse_json_request
from Employee.department_views import resolve_optional_department
from Employee.constants import (
    ALLOWED_CREATE_DESIGNATIONS,
    ALLOWED_CREATE_WITH_LOGIN_DESIGNATIONS,
    BULK_CREATE_CHUNK_SIZE,
    IMPORT_MAX_WORKERS,
    IMPORT_REQUIRED_COLUMNS,
    IMPORT_VALIDATION_CHUNK_SIZE,
    MIN_AGE_YEARS,
)
from Employee.models import Employee
from Employee.utils import (
    age_years,
    allowed_designations_for_user,
    apply_list_filters,
    employee_payload,
    get_employees_queryset,
    is_superadmin,
    normalize_df_columns,
    normalize_gender,
    office_belongs_to_organization,
    parse_date_request,
    safe_int,
    user_can_assign_designation,
    user_can_access_employee,
    user_can_access_office,
    user_can_create_employees,
    validate_and_prepare_import_row,
)
from Organization.models import Office
from Shifts.models import Shift
from Users.auth_utils import require_auth
from Users.models import User, UserRole


@require_auth
def designation_list(request):
    """GET /api/employees/designations/ — returns { designations: [{ value, label }, ...] } allowed for current user (hierarchy)."""
    allowed = allowed_designations_for_user(request.user)
    return JsonResponse({"designations": allowed}, status=200)


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def create_employee_with_login(request):
    """
    POST /api/employees/create-with-login/
    Body (JSON): organization_id, office_id, shift_id?, emp_code, name, designation (MANAGER|SUPERVISOR),
                 email, password, phone_number?, gender?, date_of_birth?, government_id_type?, government_id_value?
    Creates a User (with login) and linked Employee for Manager or Supervisor.
    """
    user = request.user
    body, err = parse_json_request(request)
    if err:
        return err

    org_id = safe_int(body.get("organization_id"))
    office_id = safe_int(body.get("office_id"))
    emp_code = (body.get("emp_code") or "").strip()
    name = (body.get("name") or "").strip()
    designation = (body.get("designation") or "").strip().upper()
    email = (body.get("email") or "").strip()
    password = body.get("password")
    phone_number = (body.get("phone_number") or "").strip()
    gender = normalize_gender((body.get("gender") or "").strip())
    date_of_birth = parse_date_request(body.get("date_of_birth"))
    government_id_type = (body.get("government_id_type") or "").strip()
    government_id_value = (body.get("government_id_value") or "").strip()
    shift_id = safe_int(body.get("shift_id"))

    if not org_id or not office_id:
        return JsonResponse({"error": "organization_id and office_id are required"}, status=400)
    if not emp_code or not name:
        return JsonResponse({"error": "emp_code and name are required"}, status=400)
    if not email:
        return JsonResponse({"error": "email is required for login"}, status=400)
    if not password:
        return JsonResponse({"error": "password is required"}, status=400)
    if len(password) < 8:
        return JsonResponse({"error": "password must be at least 8 characters"}, status=400)
    if designation not in ALLOWED_CREATE_WITH_LOGIN_DESIGNATIONS:
        return JsonResponse(
            {"error": "designation must be MANAGER or SUPERVISOR"},
            status=400,
        )

    office = Office.objects.filter(pk=office_id).prefetch_related("managers").first()
    if not office:
        return JsonResponse({"error": "Office not found"}, status=404)
    if not office_belongs_to_organization(office, org_id):
        return JsonResponse({"error": "Office must belong to organization"}, status=400)
    if not is_superadmin(user) and getattr(user, "organization_id", None) != org_id:
        return JsonResponse({"error": "Not authorized"}, status=403)
    if not user_can_create_employees(user):
        return JsonResponse(
            {"error": "Only Org Admin, Office Admin, Manager, or Supervisor can create employees."},
            status=403,
        )
    if not user_can_access_office(user, office):
        return JsonResponse({"error": "You can only add employees to your office"}, status=403)
    if not user_can_assign_designation(user, designation):
        return JsonResponse(
            {"error": "You cannot assign a designation above your level in the hierarchy."},
            status=400,
        )

    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({"error": "A user with this email already exists"}, status=400)
    if Employee.objects.filter(organization_id=org_id, emp_code=emp_code).exists():
        return JsonResponse({"error": "emp_code already exists for this organization"}, status=400)
    if email and Employee.objects.filter(organization_id=org_id, email__iexact=email).exists():
        return JsonResponse({"error": "email already exists for an employee in this organization"}, status=400)
    if phone_number and Employee.objects.filter(organization_id=org_id, phone_number=phone_number).exists():
        return JsonResponse({"error": "phone_number already exists for an employee in this organization"}, status=400)
    if (
        government_id_value
        and Employee.objects.filter(organization_id=org_id, government_id_value=government_id_value).exists()
    ):
        return JsonResponse(
            {"error": "government_id_value already exists for an employee in this organization"},
            status=400,
        )

    if date_of_birth is not None:
        age = age_years(date_of_birth)
        if age is not None and age < MIN_AGE_YEARS:
            return JsonResponse(
                {"error": f"Age must be {MIN_AGE_YEARS} or above (date of birth)"},
                status=400,
            )

    shift = None
    if shift_id:
        shift = Shift.objects.filter(pk=shift_id, office_id=office_id).first()
        if not shift:
            return JsonResponse({"error": "Shift not found or must belong to the same office"}, status=400)

    dept, derr = resolve_optional_department(body.get("department_id"), office_id)
    if derr:
        return derr

    user_role = UserRole.OFFICE_MANAGER if designation == "MANAGER" else UserRole.SUPERVISOR
    try:
        with transaction.atomic():
            new_user = User.objects.create_user(
                email=email,
                password=password,
                name=name,
                phone_number=phone_number,
                emp_code=emp_code,
                designation=designation,
                role=user_role,
            )
            new_user.organization = office.organization
            new_user.save(update_fields=["organization_id"])

            emp = Employee.objects.create(
                organization_id=org_id,
                office=office,
                shift=shift,
                department=dept,
                emp_code=emp_code,
                name=name,
                designation=designation,
                gender=gender,
                date_of_birth=date_of_birth,
                email=email,
                phone_number=phone_number,
                government_id_type=government_id_type,
                government_id_value=government_id_value,
                created_by=user,
                updated_by=user,
                user=new_user,
            )
            if designation == "MANAGER":
                office.managers.add(new_user)
    except (ValidationError, ValueError, TypeError, IntegrityError) as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse(employee_payload(emp), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class EmployeeView(View):
    """
    GET    /api/employees/       → list (auth). Filter: ?office_id=, ?organization_id=
    POST   /api/employees/       → create (auth)
    GET    /api/employees/<id>/  → detail (auth)
    PATCH  /api/employees/<id>/  → update (auth)
    DELETE /api/employees/<id>/  → delete (auth)
    """

    @method_decorator(require_auth)
    def get(self, request, pk=None):
        if pk is None:
            return self._list(request)
        return self._detail(request, pk)

    @method_decorator(require_auth)
    def post(self, request):
        return self._create(request)

    @method_decorator(require_auth)
    def patch(self, request, pk):
        return self._update(request, pk)

    @method_decorator(require_auth)
    def delete(self, request, pk):
        return self._delete(request, pk)

    def _list(self, request):
        queryset = get_employees_queryset(request.user).order_by("office", "name")
        employees = apply_list_filters(queryset, request.user, request.GET)
        return JsonResponse({"employees": [employee_payload(e) for e in employees]}, status=200)

    def _detail(self, request, pk):
        emp = Employee.objects.filter(pk=pk).select_related("organization", "office", "department").first()
        if not emp:
            return JsonResponse({"error": "Not found"}, status=404)
        # Return 404 (not 403) so we don't reveal existence to unauthorized users
        if not user_can_access_employee(request.user, emp):
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse(employee_payload(emp), status=200)

    def _parse_body(self, request):
        """Parse JSON or multipart form data."""
        if "multipart/form-data" in (request.content_type or ""):
            return request.POST, request.FILES
        data, err = parse_json_request(request)
        if err:
            return None, None
        return data, {}

    def _create(self, request):
        user = request.user
        if not is_superadmin(user) and not user.organization_id:
            return JsonResponse({"error": "Not authorized"}, status=403)
        body, files = self._parse_body(request)
        if body is None:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        org_id = safe_int(body.get("organization_id"))
        office_id = safe_int(body.get("office_id"))
        emp_code = (body.get("emp_code") or "").strip()
        name = (body.get("name") or "").strip()
        designation = (body.get("designation") or "").strip()
        gender = normalize_gender((body.get("gender") or "").strip())
        date_of_birth = parse_date_request(body.get("date_of_birth"))
        email = (body.get("email") or "").strip()
        phone_number = (body.get("phone_number") or "").strip()
        government_id_type = (body.get("government_id_type") or "").strip()
        government_id_value = (body.get("government_id_value") or "").strip()

        if not org_id:
            return JsonResponse({"error": "organization_id is required"}, status=400)
        if not office_id:
            return JsonResponse({"error": "office_id is required"}, status=400)
        if not emp_code:
            return JsonResponse({"error": "emp_code is required"}, status=400)
        if not name:
            return JsonResponse({"error": "name is required"}, status=400)

        office = Office.objects.filter(pk=office_id).prefetch_related("managers").first()
        if not office:
            return JsonResponse({"error": "Office not found"}, status=404)
        if not office_belongs_to_organization(office, org_id):
            return JsonResponse({"error": "Office must belong to organization"}, status=400)
        if not is_superadmin(user) and user.organization_id != org_id:
            return JsonResponse({"error": "Not authorized"}, status=403)
        if not user_can_create_employees(user):
            return JsonResponse(
                {"error": "Only Org Admin, Office Admin, Manager, or Supervisor can create employees."},
                status=403,
            )
        if not user_can_access_office(user, office):
            return JsonResponse({"error": "You can only add employees to your office"}, status=403)
        if designation and designation not in ALLOWED_CREATE_DESIGNATIONS:
            return JsonResponse(
                {"error": "Only Staff and Support Staff designations can be assigned when creating employees."},
                status=400,
            )
        if designation and not user_can_assign_designation(user, designation):
            return JsonResponse(
                {"error": "You cannot assign a designation above your level in the hierarchy."},
                status=400,
            )
        if not designation:
            designation = "EMPLOYEE"

        if Employee.objects.filter(organization_id=org_id, emp_code=emp_code).exists():
            return JsonResponse({"error": "emp_code already exists for this organization"}, status=400)

        shift_id = safe_int(body.get("shift_id"))
        shift = None
        if shift_id:
            shift = Shift.objects.filter(pk=shift_id, office_id=office_id).first()
            if not shift:
                return JsonResponse({"error": "Shift not found or must belong to the same office"}, status=400)

        dept, derr = resolve_optional_department(body.get("department_id"), office_id)
        if derr:
            return derr

        profile_pic = files.get("profile_pic") if files else None
        try:
            emp = Employee(
                organization_id=org_id,
                office=office,
                shift=shift,
                department=dept,
                emp_code=emp_code,
                name=name,
                designation=designation,
                gender=gender,
                date_of_birth=date_of_birth,
                email=email,
                phone_number=phone_number,
                government_id_type=government_id_type,
                government_id_value=government_id_value,
                created_by=user,
                updated_by=user,
            )
            if profile_pic:
                emp.profile_pic = profile_pic
            emp.full_clean()
            emp.save()
        except ValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)
        return JsonResponse(employee_payload(emp), status=201)

    def _update(self, request, pk):
        user = request.user
        emp = Employee.objects.filter(pk=pk).select_related("shift", "department").first()
        if not emp:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_employee(user, emp):
            return JsonResponse({"error": "Not found"}, status=404)

        body, files = self._parse_body(request)
        if body is None:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        if "designation" in body:
            new_designation = (body.get("designation") or "").strip()
            if new_designation and not user_can_assign_designation(user, new_designation):
                return JsonResponse(
                    {"error": "You cannot assign a designation above your level in the hierarchy."},
                    status=400,
                )

        update_fields = set()
        for field in ("name", "designation", "email", "phone_number"):
            if field in body:
                setattr(emp, field, (body[field] or "").strip())
                update_fields.add(field)
        if "gender" in body:
            emp.gender = normalize_gender((body.get("gender") or "").strip())
            update_fields.add("gender")
        if "date_of_birth" in body:
            emp.date_of_birth = parse_date_request(body.get("date_of_birth"))
            update_fields.add("date_of_birth")
        if "emp_code" in body:
            ec = (body.get("emp_code") or "").strip()
            if (
                ec
                and not Employee.objects.filter(organization_id=emp.organization_id, emp_code=ec)
                .exclude(pk=emp.pk)
                .exists()
            ):
                emp.emp_code = ec
                update_fields.add("emp_code")
        if "shift_id" in body:
            shift_id = safe_int(body.get("shift_id"))
            if shift_id:
                shift = Shift.objects.filter(pk=shift_id, office_id=emp.office_id).first()
                if not shift:
                    return JsonResponse({"error": "Shift not found or must belong to the same office"}, status=400)
                emp.shift = shift
            else:
                emp.shift = None
            update_fields.add("shift")
        if "office_id" in body:
            office_id_new = safe_int(body.get("office_id"))
            office = (
                Office.objects.filter(pk=office_id_new).prefetch_related("managers").first() if office_id_new else None
            )
            if not office_belongs_to_organization(office, emp.organization_id):
                return JsonResponse({"error": "Office not found or must belong to same organization"}, status=400)
            if not user_can_access_office(user, office):
                return JsonResponse({"error": "You can only assign employees to your office"}, status=403)
            emp.office_id = office_id_new
            update_fields.add("office_id")
            if emp.shift_id and emp.shift.office_id != office_id_new:
                emp.shift = None
                update_fields.add("shift")
            if emp.department_id and emp.department.office_id != office_id_new:
                emp.department = None
                update_fields.add("department")
        if "department_id" in body:
            raw = body.get("department_id")
            if raw is None or raw == "":
                emp.department = None
            else:
                dept, derr = resolve_optional_department(raw, emp.office_id)
                if derr:
                    return derr
                emp.department = dept
            update_fields.add("department")
        if emp.department_id and emp.department.office_id != emp.office_id:
            emp.department = None
            update_fields.add("department")
        if "is_active" in body:
            emp.is_active = bool(body["is_active"])
            update_fields.add("is_active")
        if "government_id_type" in body:
            emp.government_id_type = (body.get("government_id_type") or "").strip()
            update_fields.add("government_id_type")
        if "government_id_value" in body:
            emp.government_id_value = (body.get("government_id_value") or "").strip()
            update_fields.add("government_id_value")
        profile_pic = files.get("profile_pic") if files else None
        if profile_pic:
            emp.profile_pic = profile_pic
            update_fields.add("profile_pic")
        emp.updated_by = user
        update_fields.update(("updated_at", "updated_by"))
        try:
            emp.full_clean()
            emp.save(update_fields=list(update_fields))
        except ValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)
        return JsonResponse(employee_payload(emp), status=200)

    def _delete(self, request, pk):
        emp = Employee.objects.filter(pk=pk).first()
        if not emp:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_employee(request.user, emp):
            return JsonResponse({"error": "Not found"}, status=404)
        emp.delete()
        return JsonResponse({"message": "Deleted"}, status=200)


@require_auth
@require_http_methods(["GET"])
def check_employee_duplicate(request):
    """
    GET /api/employees/check-duplicate/?office_id=1&phone_number=...&email=...&government_id_value=...&exclude_employee_id=2
    Checks if any active employee in the given office has the same phone_number, email, or government_id_value.
    Returns { phone_number_taken, email_taken, government_id_value_taken } (booleans).
    """
    user = request.user
    office_id = safe_int(request.GET.get("office_id"))
    if office_id is None:
        return JsonResponse({"error": "office_id is required"}, status=400)

    office = Office.objects.filter(pk=office_id).prefetch_related("managers").first()
    if not office:
        return JsonResponse({"error": "Office not found"}, status=404)
    if not user_can_access_office(user, office):
        return JsonResponse({"error": "Not authorized for this office"}, status=403)

    phone_number = (request.GET.get("phone_number") or "").strip()
    email = (request.GET.get("email") or "").strip()
    government_id_value = (request.GET.get("government_id_value") or "").strip()
    exclude_id = safe_int(request.GET.get("exclude_employee_id"))

    # Single query with conditional aggregation (one round-trip, index-friendly).
    # Same pattern as large platforms: minimize round-trips, set-based check, no N+1.
    base = Employee.objects.filter(office_id=office_id, is_active=True)
    if exclude_id:
        base = base.exclude(pk=exclude_id)

    agg_kwargs = {}
    if phone_number:
        agg_kwargs["phone_taken"] = Max(
            Case(
                When(phone_number=phone_number, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
    if email:
        agg_kwargs["email_taken"] = Max(
            Case(
                When(email=email, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
    if government_id_value:
        agg_kwargs["govt_taken"] = Max(
            Case(
                When(government_id_value=government_id_value, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

    if not agg_kwargs:
        return JsonResponse(
            {
                "phone_number_taken": False,
                "email_taken": False,
                "government_id_value_taken": False,
            },
            status=200,
        )

    agg = base.aggregate(**agg_kwargs)
    phone_number_taken = (agg.get("phone_taken") or 0) == 1 if phone_number else False
    email_taken = (agg.get("email_taken") or 0) == 1 if email else False
    government_id_value_taken = (agg.get("govt_taken") or 0) == 1 if government_id_value else False

    return JsonResponse(
        {
            "phone_number_taken": phone_number_taken,
            "email_taken": email_taken,
            "government_id_value_taken": government_id_value_taken,
        },
        status=200,
    )


@require_auth
@require_http_methods(["GET"])
def employee_export(request):
    """GET /api/employees/export/ — CSV of employees (same filters as list), using DataFrame with index column."""
    user = request.user
    queryset = get_employees_queryset(user).order_by("office", "name")
    employees = apply_list_filters(queryset, user, request.GET)

    rows = []
    for idx, emp in enumerate(employees, start=1):
        rows.append(
            {
                "index": idx,
                "id": emp.id,
                "emp_code": emp.emp_code,
                "name": emp.name,
                "designation": emp.designation or "",
                "gender": emp.gender or "",
                "date_of_birth": emp.date_of_birth.isoformat() if emp.date_of_birth else "",
                "email": emp.email or "",
                "phone_number": emp.phone_number or "",
                "government_id_type": emp.government_id_type or "",
                "government_id_value": emp.government_id_value or "",
                "department_id": emp.department_id or "",
                "department_name": emp.department.name if emp.department_id else "",
                "office_id": emp.office_id,
                "organization_id": emp.organization_id,
                "is_active": emp.is_active,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "index",
                "id",
                "emp_code",
                "name",
                "designation",
                "gender",
                "date_of_birth",
                "email",
                "phone_number",
                "government_id_type",
                "government_id_value",
                "department_id",
                "department_name",
                "office_id",
                "organization_id",
                "is_active",
            ]
        )

    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    response = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="employees_export.csv"'
    return response


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def employee_import(request):
    """
    POST /api/employees/import/ — multipart: file (Excel/CSV), organization_id, office_id.
    Uses DataFrames, validations (required fields, age >= 18, government_id_type choices),
    duplicate checks (row duplicate, then email/phone/govt_id), and chunked bulk_create for optimization.
    """
    user = request.user
    if not user_can_create_employees(user):
        return JsonResponse(
            {"error": "Only Org Admin, Office Admin, Manager, or Supervisor can create employees."},
            status=403,
        )
    org_id = safe_int(request.POST.get("organization_id"))
    office_id = safe_int(request.POST.get("office_id"))
    if not org_id or not office_id:
        return JsonResponse({"error": "organization_id and office_id are required"}, status=400)

    office = Office.objects.filter(pk=office_id).prefetch_related("managers").first()
    if not office_belongs_to_organization(office, org_id):
        return JsonResponse({"error": "Office not found or must belong to organization"}, status=400)
    if not is_superadmin(user) and user.organization_id != org_id:
        return JsonResponse({"error": "Not authorized"}, status=403)
    if not user_can_access_office(user, office):
        return JsonResponse({"error": "You can only import employees into your office"}, status=403)

    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"error": "file is required"}, status=400)

    filename = (upload.name or "").lower()
    try:
        raw = upload.read()
    except Exception as e:
        return JsonResponse({"error": f"Failed to read file: {e}"}, status=400)

    if filename.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig", dtype=str)
        except Exception as e:
            return JsonResponse({"error": f"Invalid CSV: {e}"}, status=400)
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            df = pd.read_excel(io.BytesIO(raw), dtype=str, engine="openpyxl")
        except Exception as e:
            return JsonResponse({"error": f"Invalid Excel file: {e}"}, status=400)
    else:
        return JsonResponse({"error": "File must be .csv or .xlsx"}, status=400)

    df = normalize_df_columns(df)
    if df.empty:
        return JsonResponse({"error": "File has no data rows"}, status=400)

    for col in IMPORT_REQUIRED_COLUMNS:
        if col not in df.columns:
            return JsonResponse({"error": f"File must have required column: {col}"}, status=400)

    # Add row index (1-based, for error reporting) — file row = index + 2 (header + 1-based)
    df = df.reset_index(drop=True)
    df["_row_index"] = df.index + 2

    # 1) Drop exact duplicate rows (first occurrence kept)
    before_dedup = len(df)
    df = df.drop_duplicates(subset=list(df.columns), keep="first")
    dup_rows_dropped = before_dedup - len(df)

    # Load existing data for duplicate checks (single query each)
    existing = Employee.objects.filter(organization_id=org_id).values_list(
        "emp_code", "email", "phone_number", "government_id_value"
    )
    existing_emp_codes = {r[0] for r in existing if r[0]}
    existing_emails = {r[1] for r in existing if r[1]}
    existing_phones = {r[2] for r in existing if r[2]}
    existing_govt_ids = {r[3] for r in existing if r[3]}

    # Thread-safe: each chunk gets its own copies of "seen" sets; we merge duplicates after.
    # We'll process in chunks and collect (row_index, data, error); then deduplicate by emp_code/email/phone/govt_id in order.
    def process_chunk(chunk_df: pd.DataFrame, start_idx: int):
        seen_emp_codes = set(existing_emp_codes)
        seen_emails = set(existing_emails)
        seen_phones = set(existing_phones)
        seen_govt_ids = set(existing_govt_ids)
        results = []
        for i, (_, row) in enumerate(chunk_df.iterrows()):
            row_index = int(row.get("_row_index", start_idx + i + 2))
            r_idx, data, err = validate_and_prepare_import_row(
                row,
                row_index,
                existing_emp_codes,
                existing_emails,
                existing_phones,
                existing_govt_ids,
                seen_emp_codes,
                seen_emails,
                seen_phones,
                seen_govt_ids,
                org_id,
                office_id,
                user,
            )
            results.append((r_idx, data, err))
        return results

    all_results = []
    chunks = [df.iloc[i : i + IMPORT_VALIDATION_CHUNK_SIZE] for i in range(0, len(df), IMPORT_VALIDATION_CHUNK_SIZE)]
    with ThreadPoolExecutor(max_workers=min(IMPORT_MAX_WORKERS, len(chunks) or 1)) as executor:
        start = 0
        futures = []
        for c in chunks:
            futures.append(executor.submit(process_chunk, c, start))
            start += len(c)
        for fut in as_completed(futures):
            all_results.extend(fut.result())

    # Sort by row index and resolve cross-chunk duplicates (first occurrence wins)
    all_results.sort(key=lambda x: x[0])
    valid_data = []
    errors = []
    seen_emp_codes_final = set(existing_emp_codes)
    seen_emails_final = set(existing_emails)
    seen_phones_final = set(existing_phones)
    seen_govt_ids_final = set(existing_govt_ids)

    for row_index, data, err in all_results:
        if err:
            errors.append(err)
            continue
        if data["emp_code"] in seen_emp_codes_final:
            errors.append(f"Row {row_index}: Duplicate emp_code (skipped)")
            continue
        if data["email"] and data["email"] in seen_emails_final:
            errors.append(f"Row {row_index}: Duplicate email (skipped)")
            continue
        if data["phone_number"] and data["phone_number"] in seen_phones_final:
            errors.append(f"Row {row_index}: Duplicate phone (skipped)")
            continue
        if data["government_id_value"] and data["government_id_value"] in seen_govt_ids_final:
            errors.append(f"Row {row_index}: Duplicate government ID (skipped)")
            continue
        seen_emp_codes_final.add(data["emp_code"])
        if data["email"]:
            seen_emails_final.add(data["email"])
        if data["phone_number"]:
            seen_phones_final.add(data["phone_number"])
        if data["government_id_value"]:
            seen_govt_ids_final.add(data["government_id_value"])
        valid_data.append(data)

    # Bulk create in chunks
    created = 0
    for i in range(0, len(valid_data), BULK_CREATE_CHUNK_SIZE):
        chunk = valid_data[i : i + BULK_CREATE_CHUNK_SIZE]
        objects = [
            Employee(
                organization_id=org_id,
                office=office,
                emp_code=d["emp_code"],
                name=d["name"],
                designation=d.get("designation") or "",
                gender=d.get("gender") or "",
                date_of_birth=d.get("date_of_birth"),
                email=d.get("email") or "",
                phone_number=d.get("phone_number") or "",
                government_id_type=d.get("government_id_type") or "",
                government_id_value=d.get("government_id_value") or "",
                created_by=user,
                updated_by=user,
            )
            for d in chunk
        ]
        try:
            Employee.objects.bulk_create(objects)
            created += len(objects)
        except Exception as e:
            for d in chunk:
                errors.append(f"Bulk create failed for emp_code {d['emp_code']}: {e}")
            break

    total_rows = len(df)
    failed_count = len(errors)
    if failed_count > 0:
        message = f"{created} out of {total_rows} uploaded, {failed_count} failed because of validation error."
    else:
        message = f"{created} out of {total_rows} uploaded."
    if dup_rows_dropped:
        message += f" {dup_rows_dropped} duplicate row(s) removed from file before processing."

    return JsonResponse(
        {
            "created": created,
            "skipped_duplicate_rows": dup_rows_dropped,
            "errors": errors[:100],
            "total_errors": failed_count,
            "total_rows": total_rows,
            "message": message,
        },
        status=200,
    )
