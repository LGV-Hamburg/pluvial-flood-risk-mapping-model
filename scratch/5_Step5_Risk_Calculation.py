import arcpy
 
table = arcpy.GetParameterAsText(0)  
field_1 = arcpy.GetParameterAsText(1)
field_2 = arcpy.GetParameterAsText(2)
field_3 = arcpy.GetParameterAsText(3)
weight_1 = float(arcpy.GetParameterAsText(4).replace(",", "."))
weight_2 = float(arcpy.GetParameterAsText(5).replace(",", "."))
weight_3 = float(arcpy.GetParameterAsText(6).replace(",", "."))
new_field = arcpy.GetParameterAsText(7)
 
# Add the new field if it doesn't already exist
if new_field not in [f.name for f in arcpy.ListFields(table)]:
    arcpy.AddField_management(table, new_field, "DOUBLE")
 
with arcpy.da.UpdateCursor(
    table, [field_1, field_2, field_3, new_field]
) as cursor:
    for row in cursor:
        # Multiply the fields with their corresponding weights
        weighted_value_1 = row[0] ** weight_1 
        weighted_value_2 = row[1] ** weight_2
        weighted_value_3 = row[2] ** weight_3
 
        # Calculate the final weighted product
        product = weighted_value_1 * weighted_value_2 * weighted_value_3
 
        row[3] = product  # Assign the product to the new field
 
        cursor.updateRow(row)
