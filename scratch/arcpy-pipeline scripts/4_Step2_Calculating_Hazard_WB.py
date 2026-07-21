# !!! Make sure the table that is being edited is closed (aka can aquire lock) !!!

#Parameter 0: Path to the input table (Data Type: Table or Feature Class). - Layer with buildings
#Parameter 1: Field of Percentage flooded in 2m buffer and 20cm depth. (Data Type: Field)
#Parameter 2: Field of Percentage flooded in 2m buffer and 30cm depth. (Data Type: Field)
#Parameter 3: Field of Percentage flooded in 2m buffer and 40cm depth. (Data Type: Field)
#Parameter 4: Field of Percentage flooded in 2m buffer and 50cm depth. (Data Type: Field)
#Parameter 5: Field of Percentage flooded in 2m buffer and 60cm depth. (Data Type: Field)
#Parameter 6: Field of Percentage flooded in 2m buffer and 70cm depth. (Data Type: Field)
#Parameter 7: Field of Percentage flooded in 2m buffer and 80cm depth. (Data Type: Field)
#Parameter 8: Field of Percentage flooded in 2m buffer and 90cm depth. (Data Type: Field)
#Parameter 9: Field of Percentage flooded in 2m buffer and 100cm depth. (Data Type: Field)
#Parameter 10: New field for storing the result (Data Type: Str)
#Parameter 11: Shape parameter used for the Log_Normal distribution. (Data Type: Str)

import arcpy
import numpy as np
from scipy.stats import lognorm

# Parameters
table = arcpy.GetParameterAsText(0)  # Path to the input table
params = [arcpy.GetParameterAsText(i) for i in range(1, 10)]  # List of percentage fields
new_field = arcpy.GetParameterAsText(10)
shape = arcpy.GetParameterAsText(11)
shape = float(shape.replace(",", "."))

# Filter out empty fields
params = [p for p in params if p]  # Only keep non-empty fields

# Add the new field if it doesn't already exist
if new_field not in [f.name for f in arcpy.ListFields(table)]:
    arcpy.AddField_management(table, new_field, "DOUBLE")

# Length of rows
n = int(arcpy.GetCount_management(table)[0])

# Initialize health array with zeros
health = np.zeros(n)

# Calculate health values
if params:  # Only run if there are fields selected
    with arcpy.da.SearchCursor(table, params) as cursor:
        for i, row in enumerate(cursor):
            row = [0 if (value is None or value == '') else value for value in row]
            health[i] = sum(lognorm.cdf(row[j] * 4 / 100, shape) for j in range(len(row)))

# Update the table with the new health values
with arcpy.da.UpdateCursor(table, [new_field]) as cursor:
    for j, row in enumerate(cursor):
        cursor.updateRow((health[j],))
# Example usage:
# calculate_health_hazard(
#     input_path='Building_ExampleLayer.csv',
#     percent_fields=[
#         'P2Flood_20cm', 'P2Flood_30cm', 'P2Flood_40cm', 'P2Flood_50cm',
#         'P2Flood_60cm', 'P2Flood_70cm', 'P2Flood_80cm', 'P2Flood_90cm', 'P2Flood_100cm'
#     ],
#     new_field='HealthHazard',
#     shape=0.5,
#     output_path='Building_ExampleLayer_with_health.csv'
# )