import bpy
import os
import math
import bmesh
from .utils import *

class MeshGenerationByOffsetting(bpy.types.Operator):
    """Replacing the selected strokes with new ones whose polygons are offset"""
    bl_idname = "nijigp.mesh_generation_offset"
    bl_label = "Convert to Meshes by Offsetting"
    bl_category = 'View'
    bl_options = {'REGISTER', 'UNDO'}

    # Define properties
    offset_amount: bpy.props.FloatProperty(
            name='Offset',
            default=0.1, soft_min=0, unit='LENGTH',
            description='Offset length'
    )
    resolution: bpy.props.IntProperty(
            name='Resolution',
            default=4, min=2, max=256,
            description='Number of offsets calculated'
    )
    corner_shape: bpy.props.EnumProperty(
            name='Corner Shape',
            items=[('JT_ROUND', 'Round', ''),
                    ('JT_SQUARE', 'Square', ''),
                    ('JT_MITER', 'Miter', '')],
            default='JT_ROUND',
            description='Shape of corners generated by offsetting'
    )
    slope_style: bpy.props.EnumProperty(
            name='Corner Shape',
            items=[('LINEAR', 'Linear', ''),
                    ('SPHERE', 'Sphere', ''),
                    ('STEP', 'Step', '')],
            default='SPHERE',
            description='Slope shape of the generated mesh'
    )
    keep_original: bpy.props.BoolProperty(
            name='Keep Original',
            default=True,
            description='Do not delete the original stroke'
    )
    postprocess_double_sided: bpy.props.BoolProperty(
            name='Double-Sided',
            default=True,
            description='Make the mesh symmetric to the working plane'
    )
    postprocess_shade_smooth: bpy.props.BoolProperty(
            name='Shade Smooth',
            default=False,
            description='Enable face smooth shading and auto smooth normals'
    )
    postprocess_merge: bpy.props.BoolProperty(
            name='Merge',
            default=False,
            description='Merge vertices close to each other'   
    )
    merge_distance: bpy.props.FloatProperty(
            name='Distance',
            default=0.01,
            min=0.0001,
            unit='LENGTH',
            description='Distance used during merging'   
    )
    postprocess_remesh: bpy.props.BoolProperty(
            name='Remesh',
            default=False,
            description='Perform a voxel remesh'   
    )
    remesh_voxel_size: bpy.props.FloatProperty(
            name='Voxel Size',
            default=0.1,
            min=0.0001,
            unit='LENGTH',
            description='Voxel size used during remeshing'   
    )


    def draw(self, context):
        layout = self.layout
        layout.label(text = "Geometry Options:")
        box1 = layout.box()
        box1.prop(self, "offset_amount", text = "Offset Amount")
        box1.prop(self, "resolution", text = "Resolution")
        box1.label(text = "Corner Shape")
        box1.prop(self, "corner_shape", text = "")
        box1.label(text = "Slope Style")
        box1.prop(self, "slope_style", text = "")
        box1.prop(self, "keep_original", text = "Keep Original")

        layout.label(text = "Post-Processing Options:")
        box2 = layout.box()
        box2.prop(self, "postprocess_double_sided", text = "Double-Sided")  
        box2.prop(self, "postprocess_shade_smooth", text = "Shade Smooth")
        row = box2.row()
        row.prop(self, "postprocess_merge", text='Merge By')
        row.prop(self, "merge_distance", text='Distance')
        row = box2.row()
        row.prop(self, "postprocess_remesh", text='Remesh')
        row.prop(self, "remesh_voxel_size", text='Voxel Size')

    def execute(self, context):

        # Import and configure Clipper
        try:
            import pyclipper
        except ImportError:
            self.report({"ERROR"}, "Please install dependencies in the Preferences panel.")
        clipper = pyclipper.PyclipperOffset()
        clipper.MiterLimit = math.inf
        jt = pyclipper.JT_ROUND
        if self.corner_shape == "JT_SQUARE":
            jt = pyclipper.JT_SQUARE
        elif self.corner_shape == "JT_MITER":
            jt = pyclipper.JT_MITER
        et = pyclipper.ET_CLOSEDPOLYGON

        # Convert selected strokes to 2D polygon point lists
        current_gp_obj = context.object
        stroke_info = []
        stroke_list = []
        mesh_names = []
        for i,layer in enumerate(current_gp_obj.data.layers):
            if not layer.lock and hasattr(layer.active_frame, "strokes"):
                for j,stroke in enumerate(layer.active_frame.strokes):
                    if stroke.select:
                        stroke_info.append([stroke, i, j])
                        stroke_list.append(stroke)
                        mesh_names.append('Offset_' + layer.info + '_' + str(j))
        poly_list, scale_factor = stroke_to_poly(stroke_list, scale = True)

        def process_single_stroke(i, co_list):
            '''
            Function that processes each stroke separately
            '''
            # Calculate offsets
            clipper.Clear()
            clipper.AddPath(co_list, jt, et)
            contours = []
            vert_idx_list = []
            vert_counter = 0
            offset_interval = self.offset_amount / self.resolution * scale_factor
            for j in range(self.resolution):
                new_contour = clipper.Execute( -offset_interval * j)
                # STEP style requires duplicating each contour
                for _ in range(1 + int(self.slope_style=='STEP')):
                    contours.append( new_contour )
                    new_idx_list = []
                    for poly in new_contour:
                        num_vert = len(poly)
                        new_idx_list.append( (vert_counter, vert_counter + num_vert) )
                        vert_counter += num_vert
                    vert_idx_list.append(new_idx_list)

            # Mesh generation
            new_mesh = bpy.data.meshes.new(mesh_names[i])
            bm = bmesh.new()
            vertex_color_layer = bm.verts.layers.color.new('Color')
            edges_by_level = []
            verts_by_level = []
            
            for j,contour in enumerate(contours):
                edges_by_level.append([])
                verts_by_level.append([])
                edge_extruded = []

                # One contour may contain more than one closed loops
                for k,poly in enumerate(contour):
                    height = abs(j * offset_interval/scale_factor)
                    if self.slope_style == 'SPHERE':
                        sphere_rad = abs(self.offset_amount)
                        height = math.sqrt(sphere_rad ** 2 - (sphere_rad - height) ** 2)
                    elif self.slope_style == 'STEP':
                        height = abs( (j+1)//2 * offset_interval/scale_factor)

                        
                    for co in poly:
                        verts_by_level[-1].append(
                            bm.verts.new(vec2_to_vec3(co, height, scale_factor))
                            )
                    bm.verts.ensure_lookup_table()

                    # Connect same-level vertices
                    for v_idx in range(vert_idx_list[j][k][0],vert_idx_list[j][k][1] - 1):
                        edges_by_level[-1].append( bm.edges.new([bm.verts[v_idx],bm.verts[v_idx + 1]]) )
                    edges_by_level[-1].append( 
                            bm.edges.new([ bm.verts[vert_idx_list[j][k][0]], bm.verts[vert_idx_list[j][k][1] - 1] ]) 
                            )

                # STEP style only: connect extruding edges
                if self.slope_style=='STEP' and j%2 > 0:
                    for v_idx,_ in enumerate(verts_by_level[-1]):
                        edge_extruded.append(
                                bm.edges.new([verts_by_level[-1][v_idx], verts_by_level[-2][v_idx]])
                            )

                bm.edges.ensure_lookup_table()
                if j>0:
                    if self.slope_style=='STEP' and j%2==1:
                        bmesh.ops.edgenet_fill(bm, edges= edges_by_level[-1]+edges_by_level[-2]+edge_extruded)
                    else:
                        bmesh.ops.triangle_fill(bm, use_beauty=True, edges= edges_by_level[-1]+edges_by_level[-2])
            bmesh.ops.triangle_fill(bm, use_beauty=True, edges= edges_by_level[-1])
            bm.faces.ensure_lookup_table()
            
            # Cleanup
            to_remove = []
            for face in bm.faces:
                if len(face.verts) > 4:
                    to_remove.append(face)
            for face in to_remove:
                bm.faces.remove(face)
            
            if self.postprocess_shade_smooth:
                for f in bm.faces:
                    f.smooth = True

            # Bottom large face
            if not self.postprocess_double_sided:
                bm.faces.new(verts_by_level[0])

            bmesh.ops.recalc_face_normals(bm, faces= bm.faces)

            # Post-processing: merge
            if self.postprocess_merge:
                bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=self.merge_distance)

            # Set vertex color from the stroke's both vertex and fill colors
            fill_base_color = [1,1,1,1]
            if current_gp_obj.data.materials[stroke_list[i].material_index].grease_pencil.show_fill:
                fill_base_color[0] = current_gp_obj.data.materials[stroke_list[i].material_index].grease_pencil.fill_color[0]
                fill_base_color[1] = current_gp_obj.data.materials[stroke_list[i].material_index].grease_pencil.fill_color[1]
                fill_base_color[2] = current_gp_obj.data.materials[stroke_list[i].material_index].grease_pencil.fill_color[2]
                fill_base_color[3] = current_gp_obj.data.materials[stroke_list[i].material_index].grease_pencil.fill_color[3]
            if hasattr(stroke_list[i],'vertex_color_fill'):
                alpha = stroke_list[i].vertex_color_fill[3]
                fill_base_color[0] = fill_base_color[0] * (1-alpha) + alpha * stroke_list[i].vertex_color_fill[0]
                fill_base_color[1] = fill_base_color[1] * (1-alpha) + alpha * stroke_list[i].vertex_color_fill[1]
                fill_base_color[2] = fill_base_color[2] * (1-alpha) + alpha * stroke_list[i].vertex_color_fill[2]
            for v in bm.verts:
                v[vertex_color_layer] = [linear_to_srgb(fill_base_color[0]), linear_to_srgb(fill_base_color[1]), linear_to_srgb(fill_base_color[2]), fill_base_color[3]]

            bm.to_mesh(new_mesh)
            bm.free()

            # Object generation
            new_object = bpy.data.objects.new(mesh_names[i], new_mesh)
            bpy.context.collection.objects.link(new_object)
            new_object.parent = current_gp_obj

            # Assign material
            if "nijigp_mat" not in bpy.data.materials:
                new_mat = bpy.data.materials.new("nijigp_mat")
                new_mat.use_nodes = True
                attr_node = new_mat.node_tree.nodes.new("ShaderNodeAttribute")
                attr_node.attribute_name = 'Color'
                for node in new_mat.node_tree.nodes:
                    if node.type == "BSDF_PRINCIPLED":
                        new_mat.node_tree.links.new(node.inputs['Base Color'], attr_node.outputs['Color'])
                        new_mat.node_tree.links.new(node.inputs['Alpha'], attr_node.outputs['Alpha'])
            new_object.data.materials.append(bpy.data.materials["nijigp_mat"])

            # Post-processing: mirror
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.objects.active = new_object
            if self.postprocess_double_sided:
                new_object.modifiers.new(name="nijigp_Mirror", type='MIRROR')
                new_object.modifiers["nijigp_Mirror"].use_axis[0] = (bpy.context.scene.nijigp_working_plane == 'Y-Z')
                new_object.modifiers["nijigp_Mirror"].use_axis[1] = (bpy.context.scene.nijigp_working_plane == 'X-Z')
                new_object.modifiers["nijigp_Mirror"].use_axis[2] = (bpy.context.scene.nijigp_working_plane == 'X-Y')
                bpy.ops.object.modifier_apply("EXEC_DEFAULT", modifier = "nijigp_Mirror")
            
            # Post-processing: remesh
            if self.postprocess_remesh:
                new_object.data.remesh_voxel_size = self.remesh_voxel_size
                new_object.data.use_remesh_preserve_volume = True
                new_object.data.use_remesh_preserve_vertex_colors = True
                bpy.ops.object.voxel_remesh("EXEC_DEFAULT")

            new_object.data.use_auto_smooth = self.postprocess_shade_smooth

        for i,co_list in enumerate(poly_list):
            process_single_stroke(i, co_list)

        # Delete old strokes
        if not self.keep_original:
            for info in stroke_info:
                layer_index = info[1]
                current_gp_obj.data.layers[layer_index].active_frame.strokes.remove(info[0])

        return {'FINISHED'}
