# !!! Make sure the table that is being edited is closed (aka can aquire lock) !!!
#Parameter 0: Path to the input table (Data Type: Table or Feature Class). - Layer with buildings
#Parameter 1: Field with the central point y-values-latitude. (Data Type: Field)
#Parameter 2: Field with the central point x-values-longitude. (Data Type: Field)
#Parameter 3: Field with the mobility risk values. (Data Type: Field)
#Parameter 4: Field with the health risk values. (Data Type: Field)
#Parameter 5: Field name for the new mobility risk values. (Data Type: str)
#Parameter 6: Field name for the new health risk values. (Data Type: str)
#Parameter 7: Number of iterations. (Data Type: int)
import numpy as np
from scipy.spatial import Delaunay
import math
table = arcpy.GetParameterAsText(0)  # Path to the input table
latpos = arcpy.GetParameterAsText(1)
longpos = arcpy.GetParameterAsText(2)
mobpos = arcpy.GetParameterAsText(3)
healthpos = arcpy.GetParameterAsText(4)
newfieldmob = arcpy.GetParameterAsText(5)
newfieldhealth = arcpy.GetParameterAsText(6)
iterx = arcpy.GetParameterAsText(7)
iter = int(iterx)
# Add the new field if it doesn't already exist
if newfieldmob not in [f.name for f in arcpy.ListFields(table)]:
    arcpy.AddField_management(table, newfieldmob, "DOUBLE")
    
if newfieldhealth not in [f.name for f in arcpy.ListFields(table)]:
    arcpy.AddField_management(table, newfieldhealth, "DOUBLE")
# Length of columns
n = int(arcpy.GetCount_management(table)[0]) 
latitude=np.zeros(n)
longitude=np.zeros(n)
mobility=np.zeros(n)
health=np.zeros(n)
null_indices_mob = []
null_indices_health = []
i = 0
with arcpy.da.SearchCursor(table, [latpos, longpos, mobpos, healthpos]) as cursor:
    for row in cursor:
        latitude[i] = row[0]
        longitude[i] = row[1]
        
        if row[2] is None:  # Check for NULL in mobility
            mobility[i] = 0  # Set temporary zero for calculations
            null_indices_mob.append(i)
        else:
            mobility[i] = row[2]
        
        if row[3] is None:  # Check for NULL in health
            health[i] = 0  # Set temporary zero for calculations
            null_indices_health.append(i)
        else:
            health[i] = row[3]
        
        i += 1
        
def smoothing(values,coordinates):
    distance = []
    for i in range(len(values)):
        temp=[]
        for n in range(len(connections[i])):
            temp.append(np.linalg.norm(coordinates[connections[i][n],:]-coordinates[i,:]))
        distance.append(temp)
    distance_vector = [np.array(distance[i]) for i in range(len(values))]
    newval = np.zeros(np.shape(values))
    for i in range(len(values)):
        temp = 0
        for j in range(len(connections[i])):
            if distance_vector[i][j] > 0:
                temp+= values[connections[i][j]] * 1/distance_vector[i][j]
        norm = sum(1/d for d in distance_vector[i] if d > 0)
        if norm > 0:
            newval[i] = (values[i] + temp/norm)/(len(connections[i])+1)
        else:
            newval[i] = values[i]
    return newval
M=np.column_stack([latitude,longitude])
tri = Delaunay(M)
# Create an empty adjacency list
connections = [[] for _ in range(len(mobility))]
# Populate the adjacency list
for simplex in tri.simplices:
    for i in range(3):
        for j in range(i + 1, 3):
            p1, p2 = simplex[i], simplex[j]
            if p2 not in connections[p1]:
                connections[p1].append(p2)
            if p1 not in connections[p2]:
                connections[p2].append(p1)
for h in range(iter):
    mobility = smoothing(mobility,M)
    health = smoothing(health,M)
                    
j = 0
with arcpy.da.UpdateCursor(table, [newfieldmob, newfieldhealth]) as cursor:
    for row in cursor:
        mob_value = mobility[j] if j not in null_indices_mob else None  # Restore NULL if originally NULL
        health_value = health[j] if j not in null_indices_health else None  # Restore NULL if originally NULL
        row = (mob_value, health_value)
        cursor.updateRow(row)
        j += 1
