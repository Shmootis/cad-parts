"""Quick build123d check for the dashboard (writes no files): builds a filleted, drilled block."""
from build123d import Axis, Box, Cylinder, fillet

part = Box(60, 40, 20) - Cylinder(8, 20)
part = fillet(part.edges().group_by(Axis.Z)[-1], 2)
bb = part.bounding_box()
print(f"valid={part.is_valid} volume={part.volume:.1f} mm3 size={bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm")
