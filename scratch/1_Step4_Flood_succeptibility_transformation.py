import arcpy
import statistics
# Get parameters from ArcGIS Pro tool input
table = arcpy.GetParameterAsText(0)  # Path to the input table
svi_field = arcpy.GetParameterAsText(1)  # Field containing SVI values
output_name = arcpy.GetParameterAsText(2)  # Name of the new output field
Thresshold = float(arcpy.GetParameterAsText(3).replace(",", "."))
Trans_Form = float(arcpy.GetParameterAsText(4).replace(",", "."))
# Extract all non-null values from the SVI field
svi_values = [row[0] for row in arcpy.da.SearchCursor(table, [svi_field]) if row[0] is not None]
# Check if SVI field has values before proceeding
if not svi_values:
    arcpy.AddMessage("No valid SVI values found in the table. Script terminated.")
    raise ValueError("No valid SVI values found.")
# Calculate mean of SVI values
svi_mean = statistics.mean(svi_values)
# Add the new field if it doesn’t already exist
existing_fields = [field.name for field in arcpy.ListFields(table)]
if output_name not in existing_fields:
    arcpy.AddField_management(table, output_name, "DOUBLE")
# Update the new field with calculated values
with arcpy.da.UpdateCursor(table, [svi_field, output_name]) as cursor:
    for row in cursor:
        if row[0] is not None:
            row[1] = (row[0] + Thresshold * svi_mean) ** Trans_Form  # (SVI + mean(SVI)/4)²
            cursor.updateRow(row)
