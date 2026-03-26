import os
from index import extract_attendance
from optimizer import optimize_waivers

# Determine the directory where this script is located
script_dir = os.path.dirname(os.path.abspath(__file__))
xls_path = os.path.join(script_dir, "Student_Attendance_Demo.xls")

df, records = extract_attendance(xls_path)

# Number of desired waiver days = 3
results = optimize_waivers(records, num_waivers=3)
print(results)