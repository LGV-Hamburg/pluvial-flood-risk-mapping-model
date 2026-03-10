#Parameter 0: Path to the input table (Data Type: Table or Feature Class). - Layer with statistical units
#Parameter 1: Fields to use in the calculation (Data Type: Field, multi-value for selecting multiple fields).
#Parameter 2: Weights for each Field. Must add up to 1 and must be of the same size as the Field vector.
#Parameter 3: New field for storing the result (Data Type: Field)

import arcpy
import numpy as np
import math

# Input parameters from ModelBuilder
table = arcpy.GetParameterAsText(0)  # Path to the input table
fields = arcpy.GetParameterAsText(1).split(';')  # Fields to include in the calculation (semicolon-separated list)
weights = arcpy.GetParameterAsText(2).split(';')  # Weights to include in the calculation (semicolon-separated list)
new_field = arcpy.GetParameterAsText(3)  # The new field to store calculated values
entr = arcpy.GetParameterAsText(4)  # The new field to store calculated values
#output_fc = arcpy.GetParameterAsText(5)  # Output shapefile (path)

# Convert weights to float for calculation
weights = [float(w) for w in weights]

# Ensure the number of weights matches the number of fields
if len(weights) != len(fields):
    arcpy.AddError("The number of weights must match the number of fields.")
    raise Exception("Number of weights does not match number of fields.")

# Ensure the weights add up to one
if sum(weights) != 1:
    arcpy.AddError("The weights must add up to 1.")
    raise Exception("Weights don't add up to 1.")

# Add the new field if it doesn't already exist
if len(new_field) > 10:
    new_field = new_field[:10]  # Trim to 10 characters
    valid_fields = [f.name for f in arcpy.ListFields(table)]  # Get existing field names

valid_fields = [f.name for f in arcpy.ListFields(table)]  # Get existing field names

for field in fields:
    if field not in valid_fields:
        arcpy.AddError(f"Field '{field}' not found in {table}. Available fields: {valid_fields}")
        raise Exception(f"Invalid field: {field}")

if new_field not in [f.name for f in arcpy.ListFields(table)]:
    arcpy.AddField_management(table, new_field, "DOUBLE")  # Adjust field type if needed

# Number of records
m = int(arcpy.GetCount_management(table)[0])  # Number of rows in the table
n = len(weights)  # Number of fields (columns)

# Initialize matrices
M = np.zeros((m, n))
null_indices = set()  # Track rows with any NULL values

# Populate the M matrix with data and track NULLs
for i in range(n):
    with arcpy.da.SearchCursor(table, fields[i]) as cursor:
        for j, row in enumerate(cursor):
            if row[0] is None:
                null_indices.add(j)  # Mark rows with NULL values
                M[j, i] = 0  # Replace NULL with 0 for calculation
            else:
                M[j, i] = row[0]

# Apply weights
w = np.array(weights)
M = np.nan_to_num(M)
Zmax = np.zeros_like(M)
Zmin = np.zeros_like(M)
D = np.zeros(2 * m)
C = np.zeros(m)

# Normalize the matrix and compute Zmax/Zmin
for i in range(n):
    temp = sum(np.square(M[:, i]))
    if temp == 0:
        M[:, i] = 0
        Zmax[:, i] = M[:, i] - max(M[:, i])
        Zmin[:, i] = M[:, i] - min(M[:, i])
    else:
        M[:, i] = M[:, i] / math.sqrt(temp) * w[i]
        Zmax[:, i] = M[:, i] - max(M[:, i])
        Zmin[:, i] = M[:, i] - min(M[:, i])

# Compute distances D and closeness coefficients C
for i in range(m):
    D[2 * i] = math.sqrt(sum(np.square(Zmax[i, :])))
    D[2 * i + 1] = math.sqrt(sum(np.square(Zmin[i, :])))
    if D[2 * i] + D[2 * i + 1] != 0:
        C[i] = D[2 * i + 1] / (D[2 * i] + D[2 * i + 1])
    else:
        C[i] = 0

# Compute entropy
Entropy = np.zeros(m)
for i in range(m):
    if np.count_nonzero(M[i, :]) == n:
        row_sum = sum(M[i, :])
        if row_sum != 0:
            M[i, :] = M[i, :] / row_sum
        x = sum(M[i, j] * math.log(M[i, j]) for j in range(n) if M[i, j] > 0)
        Entropy[i] = 2 + 1 / math.log(n) * x
    else:
        Entropy[i] = 2

# Update the table with calculated values
with arcpy.da.UpdateCursor(table, new_field) as cursor:
    for j, row in enumerate(cursor):
        if j in null_indices:
            row[0] = None  # Restore NULL in output for rows with NULL inputs
        elif entr in [False, 'false', 'FALSE']:
            row[0] = C[j]
        else: 
            row[0] = C[j] * Entropy[j]
        cursor.updateRow(row)
        
# Export the input feature class as a shapefile
#arcpy.CopyFeatures_management(table, output_fc)

# Set the output parameter for ModelBuilder
#arcpy.SetParameterAsText(5, output_fc)


# Execution command
# python 1_Step2_Calculating_the_Coping_Capacity_using_TOPSIS.py \
# --input Building_ExampleLayer.CSV \
# --fields WR EDQ \
# --weights 0.5 0.5 \
# --output CCAP \
# --entropy true