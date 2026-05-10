from django.core.exceptions import ValidationError
from django.db import transaction
from django.db import IntegrityError
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from Attenova.api_utils import parse_json_request
from Organization.access import get_offices_queryset, is_superadmin, user_can_access_office
from Organization.models import Organization, Office
from Users.models import User, UserRole
from Users.auth_utils import require_auth
from Employee.models import Employee, Designation as EmployeeDesignation
from Employee.utils import parse_date_request


def _owner_payload(owner):
    """Owner (User) → API dict. Returns None if owner is None."""
    if not owner:
        return None
    return {
        "id": owner.id,
        "name": owner.name or "",
        "email": owner.email or "",
        "phone_number": owner.phone_number or "",
        "designation": owner.designation or "",
    }


def _org_payload(org):
    payload = {
        "id": org.id,
        "name": org.name,
        "address": org.address or "",
        "city": org.city or "",
        "state": org.state or "",
        "country": org.country or "",
        "pincode": org.pincode or "",
        "phone_number": org.phone_number or "",
        "email": org.email or "",
        "is_active": org.is_active,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }
    payload["owner"] = _owner_payload(org.created_by)
    return payload


def _office_payload(office):
    payload = {
        "id": office.id,
        "organization_id": office.organization_id,
        "name": office.name,
        "location": office.location or "",
        "full_address": office.full_address or "",
        "num_biometric_devices": office.num_biometric_devices,
        "manager_ids": list(office.managers.values_list("id", flat=True)),
        "is_active": office.is_active,
        "created_at": office.created_at.isoformat() if office.created_at else None,
    }
    return payload


@method_decorator(csrf_exempt, name="dispatch")
class OrganizationView(View):
    """
    GET    /api/organizations/       → list (auth). SuperAdmin: all orgs. Others: own org only.
    POST   /api/organizations/       → create (auth, SuperAdmin only)
    GET    /api/organizations/<id>/  → detail (auth). SuperAdmin: any org. Others: own org only.
    PATCH  /api/organizations/<id>/  → update (auth, SuperAdmin only)
    DELETE /api/organizations/<id>/  → delete (auth, SuperAdmin only)
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
        user = request.user
        if is_superadmin(user):
            orgs = Organization.objects.filter(is_active=True).select_related("created_by").order_by("name")
            return JsonResponse({"organizations": [_org_payload(o) for o in orgs]}, status=200)
        if not user.organization_id:
            return JsonResponse({"organizations": []}, status=200)
        org = Organization.objects.filter(id=user.organization_id, is_active=True).select_related("created_by").first()
        if not org:
            return JsonResponse({"organizations": []}, status=200)
        return JsonResponse({"organizations": [_org_payload(org)]}, status=200)

    def _detail(self, request, pk):
        user = request.user
        org = Organization.objects.filter(pk=pk).select_related("created_by").first()
        if not org:
            return JsonResponse({"error": "Not found"}, status=404)
        if not is_superadmin(user) and user.organization_id != org.id:
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse(_org_payload(org), status=200)

    def _create(self, request):
        if not is_superadmin(request.user):
            return JsonResponse({"error": "Only SuperAdmin can create organization"}, status=403)
        body, err = parse_json_request(request)
        if err:
            return err

        owner = body.get("owner") or {}
        org_data = body.get("organization") or {}

        email = (owner.get("email") or "").strip()
        password = owner.get("password")
        org_name = (org_data.get("name") or "").strip()

        if not email:
            return JsonResponse({"error": "owner.email is required"}, status=400)
        if not password:
            return JsonResponse({"error": "owner.password is required"}, status=400)
        if not org_name:
            return JsonResponse({"error": "organization.name is required"}, status=400)

        if User.objects.filter(email__iexact=email).exists():
            return JsonResponse({"error": "A user with this email already exists"}, status=400)

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    name=(owner.get("name") or "").strip(),
                    phone_number=(owner.get("phone_number") or "").strip(),
                    designation="ORG_ADMIN",
                    role=UserRole.ORG_ADMIN,
                )
                organization = Organization.objects.create(
                    name=org_name,
                    address=(org_data.get("address") or "").strip(),
                    pincode=(org_data.get("pincode") or "").strip(),
                    city=(org_data.get("city") or "").strip(),
                    state=(org_data.get("state") or "").strip(),
                    country=(org_data.get("country") or "").strip(),
                    phone_number=(org_data.get("phone_number") or "").strip(),
                    email=(org_data.get("email") or "").strip(),
                    created_by=user,
                    updated_by=user,
                )
                user.organization = organization
                user.save(update_fields=["organization"])
        except (IntegrityError, ValidationError, ValueError, TypeError) as e:
            return JsonResponse({"error": str(e)}, status=400)

        return JsonResponse(
            {"organization_id": organization.id, "user_id": user.id},
            status=201,
        )

    def _update(self, request, pk):
        user = request.user
        if not is_superadmin(user):
            return JsonResponse({"error": "Only SuperAdmin can update organization"}, status=403)
        org = Organization.objects.filter(pk=pk).select_related("created_by").first()
        if not org:
            return JsonResponse({"error": "Not found"}, status=404)

        body, err = parse_json_request(request)
        if err:
            return err

        update_fields = []
        for field in ["name", "address", "city", "state", "country", "pincode", "phone_number", "email"]:
            if field in body:
                value = (body[field] or "").strip()
                if field == "name" and not value:
                    continue
                setattr(org, field, value)
                update_fields.append(field)
        if "is_active" in body:
            org.is_active = bool(body["is_active"])
            update_fields.append("is_active")
        org.updated_by = user
        update_fields.extend(["updated_at", "updated_by"])
        org.save(update_fields=update_fields)

        return JsonResponse(_org_payload(org), status=200)

    def _delete(self, request, pk):
        user = request.user
        if not is_superadmin(user):
            return JsonResponse({"error": "Only SuperAdmin can delete organization"}, status=403)
        org = Organization.objects.filter(pk=pk).first()
        if not org:
            return JsonResponse({"error": "Not found"}, status=404)
        org.delete()
        return JsonResponse({"message": "Deleted"}, status=200)


@method_decorator(csrf_exempt, name="dispatch")
class OfficeView(View):
    """
    GET    /api/offices/       → list (auth). Filter by organization_id optional.
    POST   /api/offices/       → create with office admin (auth, SuperAdmin or ORG_ADMIN). Body: { organization_id, office: {...}, admin: {...} }
    GET    /api/offices/<id>/  → detail (auth)
    PATCH  /api/offices/<id>/  → update (auth)
    DELETE /api/offices/<id>/  → delete (auth, SuperAdmin or ORG_ADMIN)
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
        user = request.user
        offices = get_offices_queryset(user).order_by("organization", "name")
        org_id = request.GET.get("organization_id")
        if org_id:
            try:
                org_id = int(org_id)
                if is_superadmin(user) or user.organization_id == org_id:
                    offices = offices.filter(organization_id=org_id)
                else:
                    offices = offices.none()
            except (TypeError, ValueError):
                pass
        # Org Admin: can filter by office_id to see a specific office
        office_id = request.GET.get("office_id")
        if office_id and (user.role == UserRole.ORG_ADMIN or is_superadmin(user)):
            try:
                offices = offices.filter(pk=int(office_id))
            except (TypeError, ValueError):
                pass
        return JsonResponse({"offices": [_office_payload(o) for o in offices]}, status=200)

    def _detail(self, request, pk):
        office = Office.objects.filter(pk=pk).prefetch_related("managers").first()
        if not office:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(request.user, office):
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse(_office_payload(office), status=200)

    def _create(self, request):
        """Create office with office admin user. Body: organization_id, office: { name, ... }, admin: { email, password, ... }."""
        user = request.user
        if not is_superadmin(user) and not user.organization_id:
            return JsonResponse({"error": "Only Super Admin or Org Admin can create offices."}, status=403)

        body, err = parse_json_request(request)
        if err:
            return err

        org_id = body.get("organization_id")
        office_data = body.get("office") or {}
        admin_data = body.get("admin") or {}

        if not org_id:
            return JsonResponse({"error": "organization_id is required"}, status=400)
        try:
            org_id = int(org_id)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid organization_id"}, status=400)

        org = Organization.objects.filter(pk=org_id).first()
        if not org:
            return JsonResponse({"error": "Organization not found"}, status=404)
        if not is_superadmin(user) and user.organization_id != org_id:
            return JsonResponse({"error": "Not authorized for this organization"}, status=403)

        name = (office_data.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "office.name is required"}, status=400)

        email = (admin_data.get("email") or "").strip()
        password = admin_data.get("password")
        if not email:
            return JsonResponse({"error": "admin.email is required"}, status=400)
        if not password:
            return JsonResponse({"error": "admin.password is required"}, status=400)
        if len(password) < 8:
            return JsonResponse({"error": "admin.password must be at least 8 characters"}, status=400)

        if User.objects.filter(email__iexact=email).exists():
            return JsonResponse({"error": "A user with this email already exists"}, status=400)

        try:
            with transaction.atomic():
                admin_user = User.objects.create_user(
                    email=email,
                    password=password,
                    name=(admin_data.get("name") or "").strip(),
                    phone_number=(admin_data.get("phone_number") or "").strip(),
                    emp_code=(admin_data.get("emp_code") or "").strip(),
                    designation=(admin_data.get("designation") or "").strip(),
                    role=UserRole.OFFICE_ADMIN,
                )
                office = Office(
                    organization=org,
                    name=name,
                    location=(office_data.get("location") or "").strip(),
                    full_address=(office_data.get("full_address") or "").strip(),
                    num_biometric_devices=max(0, int(office_data.get("num_biometric_devices") or 0)),
                    created_by=request.user,
                    updated_by=request.user,
                )
                office.full_clean()
                office.save()
                admin_user.organization = org
                admin_user.office = office
                admin_user.save(update_fields=["organization_id", "office_id"])
                office.managers.add(admin_user)

                # Create an Employee record for the office admin (same person as User) and link it.
                admin_emp_code = (admin_data.get("emp_code") or "").strip()
                admin_name = (admin_data.get("name") or "").strip() or admin_user.name or email
                if admin_emp_code:
                    admin_gender = (admin_data.get("gender") or "").strip()
                    if admin_gender not in ("M", "F", "O"):
                        admin_gender = ""
                    Employee.objects.create(
                        organization=org,
                        office=office,
                        emp_code=admin_emp_code,
                        name=admin_name,
                        designation=EmployeeDesignation.OFFICE_ADMIN,
                        email=email,
                        phone_number=(admin_data.get("phone_number") or "").strip(),
                        gender=admin_gender,
                        government_id_type=(admin_data.get("government_id_type") or "").strip(),
                        government_id_value=(admin_data.get("government_id_value") or "").strip(),
                        date_of_birth=parse_date_request(admin_data.get("date_of_birth")),
                        created_by=request.user,
                        updated_by=request.user,
                        user=admin_user,
                    )
        except (ValidationError, ValueError, TypeError, IntegrityError) as e:
            return JsonResponse({"error": str(e)}, status=400)
        return JsonResponse(_office_payload(office), status=201)

    def _update(self, request, pk):
        user = request.user
        office = Office.objects.filter(pk=pk).first()
        if not office:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(user, office):
            return JsonResponse({"error": "Not found"}, status=404)

        body, err = parse_json_request(request)
        if err:
            return err

        update_fields = []
        for field in ["name", "location", "full_address"]:
            if field in body:
                value = (body[field] or "").strip()
                if field == "name" and not value:
                    continue
                setattr(office, field, value)
                update_fields.append(field)
        if "num_biometric_devices" in body:
            office.num_biometric_devices = max(0, int(body.get("num_biometric_devices") or 0))
            update_fields.append("num_biometric_devices")
        if "manager_ids" in body or "manager_id" in body:
            raw = body.get("manager_ids") or body.get("manager_id")
            if raw is None:
                manager_ids = []
            elif isinstance(raw, (int, str)):
                manager_ids = [int(raw)] if raw else []
            else:
                manager_ids = [int(x) for x in raw if x is not None]
            if manager_ids:
                valid = list(
                    User.objects.filter(
                        pk__in=manager_ids,
                        organization_id=office.organization_id,
                        role=UserRole.OFFICE_MANAGER,
                    ).values_list("pk", flat=True)
                )
                office.managers.set(valid)
            else:
                office.managers.clear()
        if "is_active" in body:
            office.is_active = bool(body["is_active"])
            update_fields.append("is_active")
        office.updated_by = user
        update_fields.extend(["updated_at", "updated_by"])
        try:
            office.full_clean()
            office.save(update_fields=update_fields)
        except ValidationError as e:
            return JsonResponse({"error": str(e)}, status=400)
        return JsonResponse(_office_payload(office), status=200)

    def _delete(self, request, pk):
        user = request.user
        office = Office.objects.filter(pk=pk).first()
        if not office:
            return JsonResponse({"error": "Not found"}, status=404)
        if not user_can_access_office(user, office):
            return JsonResponse({"error": "Not found"}, status=404)
        if not is_superadmin(user) and (
            user.organization_id != office.organization_id or user.role != UserRole.ORG_ADMIN
        ):
            return JsonResponse({"error": "Only SuperAdmin or org admin can delete"}, status=403)
        office.delete()
        return JsonResponse({"message": "Deleted"}, status=200)
