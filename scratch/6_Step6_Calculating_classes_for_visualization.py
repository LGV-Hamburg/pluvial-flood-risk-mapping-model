import arcpy
import numpy as np

# !!! Make sure the table that is being edited is closed (aka can acquire lock) !!!

# Parameter 0: Path to the input table (Data Type: Table or Feature Class)
# Parameter 1: Field to perform calculations on (Data Type: Field)
# Parameter 2: Number of iterations (Data Type: Integer)

table = arcpy.GetParameterAsText(0)
field = arcpy.GetParameterAsText(1)
n_iterations = int(arcpy.GetParameterAsText(2))

# Read values from the table
values = []
with arcpy.da.SearchCursor(table, [field]) as cursor:
    for row in cursor:
        if row[0] is not None:  # Ignore NULL values
            values.append(row[0])

# Ensure there are values to process
if not values:
    arcpy.AddError("No valid values found in the specified field.")
    raise ValueError("No valid values found in the specified field.")

values = np.array(values)
means = []

# Perform iterative calculations
for _ in range(n_iterations):
    mean_all = np.mean(values)
    means.append(mean_all)
    
    values = values[values > mean_all]  # Select values greater than the mean
    
    if len(values) == 0:
        arcpy.AddWarning("No values remain above the mean in an iteration.")
        break

# Output the calculated means as a message
arcpy.AddMessage(f"Calculated Means: {means}")

# Example usage:
# calculate_classes_for_visualization(
#     input_path='Building_ExampleLayer.csv',
#     field='Risk',
#     n_iterations=5
# )