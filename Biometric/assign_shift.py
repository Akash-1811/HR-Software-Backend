from Employee.models import Employee
from Shifts.models import Shift
import random

emps = list(Employee.objects.filter(is_active=True, shift__isnull=True).select_related("office"))
if not emps:
    print("No employees without a shift.")
else:
    random.shuffle(emps)
    n = len(emps)
    n_night = max(1, round(0.25 * n))
    n_regular = n - n_night
    night_indices = set(random.sample(range(n), n_night))

    updated = 0
    for i, emp in enumerate(emps):
        office_shifts = Shift.objects.filter(office=emp.office, is_active=True)
        reg = office_shifts.filter(is_night_shift=False).first()
        nyt = office_shifts.filter(is_night_shift=True).first()
        if not reg or not nyt:
            print(f"No shifts for office {emp.office.name}, skipping {emp.name}")
            continue
        emp.shift = nyt if i in night_indices else reg
        emp.save()
        updated += 1
    print(f"Assigned shifts to {updated} employees: {n_night} night, {n_regular} regular.")