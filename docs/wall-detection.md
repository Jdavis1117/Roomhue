# How walls are found

`detect_surfaces` in `app/detect.py` turns a room photo into separate wall, ceiling, and floor masks.

## 1. What is wall? (`app/segment.py`)

A scene-parsing network labels every pixel. It is UperNet with a ConvNeXt-T backbone, trained on ADE20K's 150 indoor and outdoor classes, so it knows a beige sofa in front of a beige wall is a sofa, and that windows, doors, curtains, art, and furniture aren't wall.

- The photo is scaled to 576 px on its long side and padded to a square; the network answers at a quarter of that.
- Wall, ceiling, and floor probabilities are upsampled and snapped to the photo's own edges with a guided filter, so masks follow real corners and trim instead of the network's coarse grid.
- About 0.8 s per photo on two CPU cores, about 600 MB peak memory.

## 2. Which wall is which? (`app/detect.py`)

The wall area is split at the room's corners. Candidate corners come from long near-vertical lines inside the wall area and from kinks where the ceiling or floor line changes direction. A candidate becomes a corner when:

- the ceiling and floor lines both bend there (joining the two bends gives the corner's exact angle), or
- a sharp bend in either line lines up with even a faint change in brightness, or
- a long, well-supported line has a clear brightness or color change across it.

It is rejected when one side of it isn't wall for much of its length (a window, door, or picture frame), when it leans more than a level camera allows for its position, or when the ceiling or floor line runs straight through it. Of two candidates close together, the better-supported one wins.

Pieces of one wall that furniture or a window cut apart are joined again when they sit between the same corners and their paint matches. Patches inside a wall that the network doubted but that match the wall's color and texture are filled; art and switches stay out.

Every wall pixel is given to exactly one wall (cut lines go to the nearest side), and thin leftover gaps go to the nearest surface, so neighbouring walls meet without an unpainted seam.

## 3. Painting where walls meet

Each surface's mask is softened by about 1.5 px. Where two surfaces meet, both soft edges are partly on; their weights are shared out rather than stacked, so the seam gets paint from both walls and none of the original photo. The same rule is in `blend_layers` (`app/color_math.py`) and `compositeBlend` (`static/app.js`).

## Fallback

If the model file is missing or `onnxruntime` isn't installed, or `ROOMHUE_DETECTOR=classic` is set, the older color-and-edge detector (`_classic_surfaces`) runs instead.

## Model file and licenses

- `models/surfaces-upernet-convnext-tiny-int8.onnx` (about 60 MB) is `openmmlab/upernet-convnext-tiny`, MIT license, exported to ONNX and quantized to int8 by `scripts/export_model.py`.
- The weights were trained on ADE20K, whose images carry their own terms for the dataset. That is normal for published segmentation models, but worth a legal look before relying on RoomRoller commercially.
- `onnxruntime` is MIT licensed.
- The sample room, `data/sample/living-room.jpg`, is "Living room in apartment of Condomínio do Edifício Zaher, Le Blond, Rio de Janeiro, Brazil" from Wikimedia Commons, released under CC0.

## Checking a change

`tests/test_detect.py` covers the real sample photo (sofas and window left out, back wall found, no surface overlaps) and a gap test that feeds the splitter a perfect scene map and requires every wall pixel to belong to exactly one wall. For a broader look, run the detector over a folder of room photos and compare overlays before and after.
