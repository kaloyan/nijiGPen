import bpy
import math
from mathutils import *
from .utils import *

def stroke_to_kdtree(co_list):
    n = len(co_list)
    kdt = kdtree.KDTree(n)
    for i in range(n):
        kdt.insert(vec2_to_vec3(co_list[i]), i)
    kdt.balance()
    return kdt

def fit_2d_strokes(strokes, search_radius, smoothness_factor = 1, pressure_delta = 0, closed = False, operator = None):
    '''
    Fit points from multiple strokes to a single curve, by executing the following operations:
        1. Delaunay triangulation
        2. Euclidean minimum spanning tree
        3. Longest path in the tree
        4. Offset based on points in the neighborhood
        5. Post-processing: vertex smooth or B-spline fitting
    '''
    try:
        import triangle as tr
        from scipy.sparse.csgraph import minimum_spanning_tree
        from scipy.interpolate import splprep, splev
    except:
        if operator:
            operator.report({"ERROR"}, "Please install dependencies in the Preferences panel.")
        return None, None
    import numpy as np

    # Create a KDTree for point attribute lookup
    poly_list, scale_factor = stroke_to_poly(strokes)
    total_point_count = 0
    for i,stroke in enumerate(strokes):
        if len(stroke.points)<2:
            continue
        total_point_count += len(stroke.points)

    if total_point_count<3:
        return None, None
    kdt = kdtree.KDTree(total_point_count)
    kdt_tangent_list = []
    kdt_stroke_list = []
    kdt_idx = 0
    co_set = set()

    # Record point attributes and prepare the input of Step 1
    # Ignore single-point strokes and merge points with the same coordinates
    tr_input = dict(vertices = [])
    for i,stroke in enumerate(strokes):
        if len(stroke.points)<2:
            continue
        for j,point in enumerate(stroke.points):
            if (poly_list[i][j][0],poly_list[i][j][1]) not in co_set:
                co_set.add((poly_list[i][j][0],poly_list[i][j][1]))
                kdt.insert(vec2_to_vec3(poly_list[i][j],0,1), kdt_idx)
                kdt_idx += 1
                if j>0:
                    kdt_tangent_list.append((poly_list[i][j][0]-poly_list[i][j-1][0],
                                            poly_list[i][j][1]-poly_list[i][j-1][1]))
                else:
                    kdt_tangent_list.append((poly_list[i][j+1][0]-poly_list[i][j][0],
                                            poly_list[i][j+1][1]-poly_list[i][j][1]))    
                kdt_stroke_list.append(i)
                tr_input['vertices'].append(poly_list[i][j])                    
    kdt.balance()

    # Triangulation and spanning tree conversion
    tr_output = tr.triangulate(tr_input, '')
    def e_dist(i,j):
        src = tr_output['vertices'][i]
        dst = tr_output['vertices'][j]
        return np.sqrt((dst[0]-src[0])**2 + (dst[1]-src[1])**2)
    num_vert = len(tr_output['vertices'])
    dist = np.zeros((num_vert, num_vert))
    for f in tr_output['triangles']:
        dist[f[1], f[0]] = e_dist(f[0], f[1])
        dist[f[2], f[0]] = e_dist(f[0], f[2])
        dist[f[1], f[2]] = e_dist(f[2], f[1])
        dist[f[0], f[1]] = e_dist(f[0], f[1])
        dist[f[0], f[2]] = e_dist(f[0], f[2])
        dist[f[2], f[1]] = e_dist(f[2], f[1])
    mst = minimum_spanning_tree(dist).toarray()
    mst = np.maximum(mst, mst.transpose())

    # Find the longest path in the tree by executing DFS twice
    def tree_dfs(mat, node, parent):
        dist_sum = 0
        path = []
        for i, value in enumerate(mat[node]):
            if value > 0 and i!=parent:
                child_sum, child_path = tree_dfs(mat, i, node)
                if dist_sum < child_sum + value:
                    dist_sum = child_sum + value
                    path = child_path
        return dist_sum, [node]+path    
    _, path_half = tree_dfs(mst, 0, None)
    total_length, path_whole = tree_dfs(mst, path_half[-1], None)    

    # Get the points in the tree as the input of postprocessing
    co_raw = np.zeros((len(path_whole), 2))
    pressure_raw = np.ones(len(path_whole))
    for i,key in enumerate(path_whole):
        co = tr_output['vertices'][key]
        co_raw[i][0] = co[0]
        co_raw[i][1] = co[1]

    # Apply offsets in the normal direction if there are points in the neighborhood
    for i,co in enumerate(co_raw):
        self_vec ,self_idx,_ = kdt.find(vec2_to_vec3(co,0,1))
        self_vec = vec3_to_vec2(self_vec)
        self_stroke = kdt_stroke_list[self_idx]
        unit_normal_vector = Vector(kdt_tangent_list[self_idx]).orthogonal().normalized()
        sum_normal_offset = 0
        neighbors = kdt.find_range(vec2_to_vec3(co,0,1), search_radius)
        for neighbor in neighbors:
            if kdt_stroke_list[neighbor[1]]!=self_stroke:
                normal_dist = vec3_to_vec2(neighbor[0]) - self_vec
                normal_dist = normal_dist.dot(unit_normal_vector)
                sum_normal_offset += normal_dist
                pressure_raw[i] += pressure_delta
        sum_normal_offset /= len(neighbors)
        co_raw[i] += unit_normal_vector * sum_normal_offset

    # Postprocessing: B-spline fitting
    if smoothness_factor is None:
        return co_raw, pressure_raw

    pressure_index = np.linspace(0,1,len(path_whole))
    if closed:
        co_raw = np.append(co_raw, [co_raw[0]], axis=0)
        pressure_index = np.append(pressure_index, 0)
        pressure_raw = np.append(pressure_raw, pressure_raw[0])
    tck, u = splprep([co_raw[:,0], co_raw[:,1]], s=total_length**2 * smoothness_factor * 0.001, per=closed)
    co_fit = np.array(splev(u, tck)).transpose()    
    tck2, u2 = splprep([pressure_index, pressure_raw], per=closed)
    pressure_fit = np.array(splev(u2, tck2))[1]

    return co_fit, pressure_fit

def distance_to_another_stroke(co_list1, co_list2, kdt2 = None, angular_tolerance = math.pi/4, correct_orientation = True):
    '''
    Calculating the similarity between two lines
    '''
    import numpy as np
    # Some algorithms do not support infinity; use a finite number instead.
    no_similarity = 65535

    n1 = len(co_list1)
    n2 = len(co_list2)
    if n1<2 or n2<2:
        return no_similarity

    # Generate the KDTrees if not provided
    if not kdt2:
        kdt2 = kdtree.KDTree(n2)
        for i in range(n2):
            kdt2.insert(vec2_to_vec3(co_list2[i]), i)
        kdt2.balance()        

    # Get point-wise distance values
    idx_arr = np.zeros(n1, dtype='int')
    dist_arr = np.zeros(n1)
    for i in range(n1):
        _, idx_arr[i], dist_arr[i] = kdt2.find(vec2_to_vec3(co_list1[i]))

    # Calculate the orientation difference of two lines
    contact_idx1 = np.argmin(dist_arr)
    contact_idx2 = min(idx_arr[contact_idx1], n2-2)
    contact_idx1 = min(contact_idx1, n1-2)
    direction1 = Vector(co_list1[contact_idx1+1]) - Vector(co_list1[contact_idx1])
    direction2 = Vector(co_list2[contact_idx2+1]) - Vector(co_list2[contact_idx2])
    angle_diff = direction1.angle(direction2)

    # Three cases of directions: similar, opposite or different
    end2 = n2-1
    if correct_orientation and angle_diff > math.pi / 2:
        angle_diff = math.pi - angle_diff
        end2 = 0
    if angle_diff > angular_tolerance:
        return no_similarity
    
    # Calculate the total cost
    total_cost, total_count = 0.0, 0.0
    for i in range(n1):
        total_cost += dist_arr[i]
        total_count += 1
        if idx_arr[i] == end2:
            break
    return total_cost/total_count
      
class CommonFittingConfig:
    """
    Options shared by all line-fitting operators
    """
    line_sampling_size: bpy.props.IntProperty(
            name='Line Spacing',
            description='Strokes with gap smaller than this may be merged',
            default=50, min=1, soft_max=100, subtype='PIXEL'
    )
    closed: bpy.props.BoolProperty(
            name='Closed Stroke',
            default=False,
            description='Treat selected strokes as a closed shape'
    )
    pressure_variance: bpy.props.FloatProperty(
            name='Pressure Variance',
            description='Increase the point radius at positions where lines are repeatedly drawn',
            default=5, soft_max=20, min=0, subtype='PERCENTAGE'
    )
    max_pressure: bpy.props.FloatProperty(
            name='Maximum Pressure',
            description='Upper bound of the point radius',
            default=150, soft_max=200, min=100, subtype='PERCENTAGE'
    )
    line_width: bpy.props.IntProperty(
            name='Base Line Width',
            description='The minimum width of the newly generated stroke',
            default=10, min=1, soft_max=100, subtype='PIXEL'
    )   
    postprocessing_method: bpy.props.EnumProperty(
        name='Methods',
        description='Algorithms to generate a smooth stroke',
        options={'ENUM_FLAG'},
        items = [
            ('SPLPREP', 'B-Spline', ''),
            ('RESAMPLE', 'Resample', '')
            ],
        default={'SPLPREP'}
    )
    b_smoothness: bpy.props.FloatProperty(
            name='B-Spline Smoothness',
            description='Smoothness factor when applying the B-spline fitting algorithm',
            default=1, soft_max=100, min=0
    )
    resample_length: bpy.props.FloatProperty(
            name='Resample Length',
            description='',
            default=0.02, min=0
    )
    smooth_repeat: bpy.props.IntProperty(
            name='Smooth Repeat',
            description='',
            default=2, min=1, max=1000
    )
    output_layer: bpy.props.StringProperty(
        name='Output Layer',
        description='Draw the new stroke in this layer. If empty, draw to the active layer',
        default='',
        search=lambda self, context, edit_text: [layer.info for layer in context.object.data.layers]
    )
    output_material: bpy.props.StringProperty(
        name='Output Material',
        description='Draw the new stroke using this material. If empty, use the active material',
        default='',
        search=lambda self, context, edit_text: [material.name for material in context.object.data.materials if material]
    )
    keep_original: bpy.props.BoolProperty(
            name='Keep Original',
            default=True,
            description='Do not delete the original stroke'
    )
    
class FitSelectedOperator(CommonFittingConfig, bpy.types.Operator):
    """Fit select strokes or points to a new stroke"""
    bl_idname = "gpencil.nijigp_fit_selected"
    bl_label = "Single-Line Fit"
    bl_category = 'View'
    bl_options = {'REGISTER', 'UNDO'}    

    def draw(self, context):
        layout = self.layout
        layout.label(text = "Input Options:")
        box1 = layout.box()
        box1.prop(self, "line_sampling_size")
        box1.prop(self, "closed")
        
        layout.label(text = "Post-Processing Options:")
        box2 = layout.box()
        row = box2.row()
        row.prop(self, "postprocessing_method")  
        if 'SPLPREP' in self.postprocessing_method:
            box2.prop(self, "b_smoothness")
        if 'RESAMPLE' in self.postprocessing_method:
            box2.prop(self, "resample_length")
            box2.prop(self, "smooth_repeat")

        layout.label(text = "Output Options:")
        box3 = layout.box()   
        box3.prop(self, "line_width")
        box3.prop(self, "pressure_variance")
        box3.prop(self, "max_pressure")
        box3.prop(self, "output_layer", text='Layer', icon='OUTLINER_DATA_GP_LAYER')
        box3.prop(self, "output_material", text='Material', icon='MATERIAL')
        box3.prop(self, "keep_original")

    def execute(self, context):
        import numpy as np

        # Get input strokes
        gp_obj = context.object
        stroke_list = []
        for i,layer in enumerate(gp_obj.data.layers):
            if layer.active_frame and not layer.lock:
                for stroke in layer.active_frame.strokes:
                    if stroke.select:
                        stroke_list.append(stroke)
        
        # Execute the fitting function
        b_smoothness = self.b_smoothness if 'SPLPREP' in self.postprocessing_method else None
        co_list, pressure_list = fit_2d_strokes(stroke_list, 
                                                search_radius=self.line_sampling_size/LINE_WIDTH_FACTOR, 
                                                smoothness_factor=b_smoothness, 
                                                pressure_delta=self.pressure_variance * 0.01,
                                                closed = self.closed,
                                                operator=self)
        if co_list is None:
            return {'FINISHED'}

        if not self.keep_original:
            bpy.ops.gpencil.delete()

        # Turn fitting output to a new stroke
        output_layer = gp_obj.data.layers.active
        if len(self.output_layer) > 0:
            for layer in gp_obj.data.layers:
                if layer.info == self.output_layer:
                    output_layer = layer
        if not output_layer.active_frame:
            output_frame = output_layer.frames.new(context.scene.frame_current)
        else:
            output_frame = output_layer.active_frame

        output_material_idx = gp_obj.active_material_index
        if len(self.output_material) > 0:
            for i,material_slot in enumerate(gp_obj.material_slots):
                if material_slot.material and material_slot.material.name == self.output_material:
                    output_material_idx = i

        new_stroke: bpy.types.GPencilStroke = output_frame.strokes.new()
        new_stroke.material_index = output_material_idx
        new_stroke.line_width = self.line_width
        new_stroke.points.add(co_list.shape[0])
        for i,point in enumerate(new_stroke.points):
            point.co = vec2_to_vec3(co_list[i], depth=0, scale_factor=1)
            point.pressure = min(pressure_list[i], self.max_pressure*0.01)
        bpy.ops.gpencil.select_all(action='DESELECT')
        new_stroke.use_cyclic = self.closed
        new_stroke.select = True
        bpy.ops.transform.translate()

        # Post-processing
        if 'RESAMPLE' in self.postprocessing_method:
            bpy.ops.gpencil.stroke_sample(length=self.resample_length)
            bpy.ops.gpencil.stroke_smooth(repeat=self.smooth_repeat)

        return {'FINISHED'}
    
class SelectSimilarOperator(bpy.types.Operator):
    """Find similar strokes with the selected ones that may belong to the same part of the drawing"""
    bl_idname = "gpencil.nijigp_select_similar"
    bl_label = "Select Similar"
    bl_category = 'View'
    bl_options = {'REGISTER', 'UNDO'}  

    line_sampling_size: bpy.props.IntProperty(
            name='Line Spacing',
            description='Strokes with gap smaller than this may be regarded similar',
            default=50, min=1, soft_max=100, subtype='PIXEL'
    )
    angular_tolerance: bpy.props.FloatProperty(
            name='Angular Tolerance',
            description='Two lines will not be regarded as similar with their directions deviated more than this value',
            default=math.pi/3, min=math.pi/18, max=math.pi/2, unit='ROTATION'
    )
    same_material: bpy.props.BoolProperty(
            name='Same Material',
            description='Ignore strokes with materials different from selected ones',
            default=True
    )
    repeat: bpy.props.BoolProperty(
            name='Repeat',
            description='Keep expanding selection until no new similar strokes found',
            default=False
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "line_sampling_size")
        layout.prop(self, "angular_tolerance")
        layout.prop(self, "same_material")
        layout.prop(self, "repeat")

    def execute(self, context):
        gp_obj: bpy.types.Object = context.object

        # Get the scope of searching
        frame_list, frame_set = [], set()
        stroke_list, stroke_set = [], set()
        for layer in gp_obj.data.layers:
            for frame in layer.frames:
                for stroke in frame.strokes:
                    if stroke.select and not is_stroke_locked(stroke, gp_obj):
                        stroke_list.append(stroke)
                        frame_list.append(frame)
                        stroke_set.add(stroke)
                        frame_set.add(frame)

        # Initialization
        poly_list, _ = stroke_to_poly(stroke_list, scale = False, correct_orientation = False)
        kdt_list = []

        for i,stroke in enumerate(stroke_list):
            kdt_list.append(stroke_to_kdtree(poly_list[i]))

        # Check every stroke in target frames
        while True:
            new_strokes = []
            new_stroke_frames= []
            for frame in frame_set:
                for stroke in frame.strokes:
                    tmp, _ = stroke_to_poly([stroke], scale = False, correct_orientation = False)
                    co_list = tmp[0]
                    kdt = stroke_to_kdtree(co_list)
                    for i,src_stroke in enumerate(stroke_list):
                        if frame_list[i] != frame:
                            continue
                        if self.same_material and src_stroke.material_index != stroke.material_index:
                            continue
                        if not overlapping_bounding_box(stroke, src_stroke):
                            continue

                        line_dist1 = distance_to_another_stroke(co_list, poly_list[i], kdt_list[i], self.angular_tolerance)
                        line_dist2 = distance_to_another_stroke(poly_list[i], co_list, kdt, self.angular_tolerance)
                        if min(line_dist1, line_dist2) < self.line_sampling_size / LINE_WIDTH_FACTOR:
                            stroke.select = True
                            # In repeat selection mode, add the stroke's information for the next iteration
                            if stroke not in stroke_set and self.repeat:
                                stroke_set.add(stroke)
                                new_strokes.append(stroke)
                                new_stroke_frames.append(frame)
                                poly_list.append(co_list)
                                kdt_list.append(kdt)
                            break
            if len(new_strokes) < 1:
                break
            stroke_list += new_strokes
            frame_list += new_stroke_frames

        return {'FINISHED'}
    
class ClusterAndFit(CommonFittingConfig, bpy.types.Operator):
    """Dividing select strokes into clusters and fit each of them to a new stroke"""
    bl_idname = "gpencil.nijigp_cluster_and_fit"
    bl_label = "Multi-Line Fit"
    bl_category = 'View'
    bl_options = {'REGISTER', 'UNDO'}    

    cluster_criterion: bpy.props.EnumProperty(
            name='Criterion',
            items=[ ('DIST', 'By Distance', ''),
                    ('NUM', 'By Number', '')],
            default='DIST',
            description='The criterion determining how many clusters selected strokes will be divided into'
    )
    cluster_dist: bpy.props.FloatProperty(
            name='Min Distance',
            default=0.05, min=0,
            unit='LENGTH',
            description='The mininum distance between two clusters'
    ) 
    cluster_num: bpy.props.IntProperty(
            name='Max Number',
            default=5, min=1,
            description='The maximum number of clusters'
    )

    def draw(self, context):
        layout = self.layout

        layout.label(text = "Clustering Options:")
        box0 = layout.box()
        box0.prop(self, "cluster_criterion")
        if self.cluster_criterion == 'DIST':
            box0.prop(self, "cluster_dist")
        else:
            box0.prop(self, "cluster_num")

        layout.label(text = "Input Options:")
        box1 = layout.box()
        box1.prop(self, "line_sampling_size")
        box1.prop(self, "closed")
        
        layout.label(text = "Post-Processing Options:")
        box2 = layout.box()
        row = box2.row()
        row.prop(self, "postprocessing_method")  
        if 'SPLPREP' in self.postprocessing_method:
            box2.prop(self, "b_smoothness")
        if 'RESAMPLE' in self.postprocessing_method:
            box2.prop(self, "resample_length")
            box2.prop(self, "smooth_repeat")

        layout.label(text = "Output Options:")
        box3 = layout.box()   
        box3.prop(self, "line_width")
        box3.prop(self, "pressure_variance")
        box3.prop(self, "max_pressure")
        box3.prop(self, "output_layer", text='Layer', icon='OUTLINER_DATA_GP_LAYER')
        box3.prop(self, "output_material", text='Material', icon='MATERIAL')
        box3.prop(self, "keep_original")

    def execute(self, context):
        import numpy as np
        try:
            from scipy.cluster.hierarchy import linkage, fcluster
        except ImportError:
            self.report({"ERROR"}, "Please install dependencies in the Preferences panel.")
            return {'FINISHED'}

        # Get input strokes
        gp_obj = context.object
        stroke_list = []
        for i,layer in enumerate(gp_obj.data.layers):
            if layer.active_frame and not layer.lock:
                for stroke in layer.active_frame.strokes:
                    if stroke.select:
                        stroke_list.append(stroke)
        if len(stroke_list)<2:
            self.report({"INFO"}, "Please select at least two strokes.")
            return {'FINISHED'}

        # Get stroke information
        poly_list, _ = stroke_to_poly(stroke_list, scale = False, correct_orientation = False)
        kdt_list = []
        for co_list in poly_list:
            kdt_list.append(stroke_to_kdtree(co_list))

        # Get stroke distance matrix
        dist_mat = []
        for i,co_list1 in enumerate(poly_list):
            for j,co_list2 in enumerate(poly_list):
                if i<j:
                    dist1 = distance_to_another_stroke(poly_list[i], poly_list[j], kdt_list[j])
                    dist2 = distance_to_another_stroke(poly_list[j], poly_list[i], kdt_list[i])
                    dist_mat.append(min(dist1,dist2))

        # Hierarchy clustering algorithm
        linkage_mat = linkage(dist_mat, method='single')
        if self.cluster_criterion == 'DIST':
            cluster_res = fcluster(linkage_mat, self.cluster_dist, criterion='distance')
        else:
            cluster_res = fcluster(linkage_mat, self.cluster_num, criterion='maxclust')
                 
        # Place strokes in clusters
        cluster_map = {}
        for i,stroke in enumerate(stroke_list):
            cluster_idx = cluster_res[i]
            if cluster_idx not in cluster_map:
                cluster_map[cluster_idx] = []
            cluster_map[cluster_idx].append(stroke)

        # For debugging: mark clusters with colors
        '''
        print(cluster_res)
        for cluster in cluster_map:
            color_mark = [np.random.rand(),np.random.rand(),np.random.rand()]
            for stroke in cluster_map[cluster]:
                for point in stroke.points:
                    point.vertex_color[0] = color_mark[0]
                    point.vertex_color[1] = color_mark[1]
                    point.vertex_color[2] = color_mark[2]
                    point.vertex_color[3] = 1
                    point.strength = 1
        '''

        # Process each cluster one by one
        generated_strokes = []
        for cluster in cluster_map:
            bpy.ops.gpencil.select_all(action='DESELECT')
            for stroke in cluster_map[cluster]:
                stroke.select = True
            bpy.ops.gpencil.nijigp_fit_selected(line_sampling_size = self.line_sampling_size,
                                            closed = self.closed,
                                            pressure_variance = self.pressure_variance,
                                            max_pressure = self.max_pressure,
                                            line_width = self.line_width,
                                            postprocessing_method = self.postprocessing_method,
                                            b_smoothness = self.b_smoothness,
                                            resample_length = self.resample_length,
                                            smooth_repeat = self.smooth_repeat,
                                            output_layer = self.output_layer,
                                            output_material = self.output_material,
                                            keep_original = self.keep_original)
            # Record the stroke selection status
            for i,layer in enumerate(gp_obj.data.layers):
                if layer.active_frame and not layer.lock:
                    for stroke in layer.active_frame.strokes:
                        if stroke.select:
                            generated_strokes.append(stroke)
        
        # Select all generated strokes
        for stroke in generated_strokes:
            stroke.select = True
        return {'FINISHED'}