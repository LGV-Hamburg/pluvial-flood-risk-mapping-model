# !!! Make sure the table that is being edited is closed (aka can aquire lock) !!!

#Parameter 0: Path to the input table (Data Type: Table or Feature Class). - Layer with buildings
#Parameter 1: Field of Area flooded in 5m buffer. (Data Type: Field)
#Parameter 2: Field of Percentage flooded in 5m buffer. (Data Type: Field)
#Parameter 3: Field of Area flooded in 15m buffer intersected with street layer. (Data Type: Field)
#Parameter 4: Field of Percentage flooded in 17.5m buffer intersected with street layer. (Data Type: Field)
#Parameter 5: Field of Area flooded in 15m-30m buffer intersected with street layer. (Data Type: Field)
#Parameter 6: Field of Percentage flooded in 15m-30m buffer intersected with street layer. (Data Type: Field)
#Parameter 7: New field for storing the result (Data Type: Str)
#Parameter 8: Minimum overlap area of streets and buffer required to be considered. (Data Type: Str)
#Parameter 9: Shape parameter used for the Log_Normal distribution. (Data Type: Str)
import pandas
import numpy as np
import math
from scipy.stats import lognorm

# ArcPy parameters
table = arcpy.GetParameterAsText(0)
area_5m = arcpy.GetParameterAsText(1)
per_5m = arcpy.GetParameterAsText(2)
area_15m = arcpy.GetParameterAsText(3)
per_15m = arcpy.GetParameterAsText(4)
area_30m = arcpy.GetParameterAsText(5)
per_30m = arcpy.GetParameterAsText(6)
new_field = arcpy.GetParameterAsText(7)
minarea = float(arcpy.GetParameterAsText(8).replace(",", "."))
shape = float(arcpy.GetParameterAsText(9).replace(",", "."))

# Add the new field if it doesn't exist
if new_field not in [f.name for f in arcpy.ListFields(table)]:
    arcpy.AddField_management(table, new_field, "DOUBLE")

# Get row count
n = int(arcpy.GetCount_management(table)[0]) 

# Initialize array
mobility = np.zeros(n)

i = 0
with arcpy.da.SearchCursor(
    table, [area_5m, per_5m, area_15m, per_15m, area_30m, per_30m]
) as cursor:
    for row in cursor:
        # Replace NULL values with 0
        row = [0 if value is None else value for value in row]
        
        # Perform calculations
        temp1 = 0 if (row[4] > 0 and row[4] / row[5] * 100 <= minarea) else row[5] / 100
        temp2 = 0 if (row[2] > 0 and row[2] / row[3] * 100 <= minarea) else row[3] / 100
        temp3 = row[1] / 100
        
        # Calculate mobility risk
        mobility[i] = lognorm.cdf(max(temp1, temp2, temp3) * 4, shape)
        i += 1

# Update the table with calculated values
j = 0
with arcpy.da.UpdateCursor(table, new_field) as cursor:
    for row in cursor:
        cursor.updateRow((mobility[j],))
        j += 1
