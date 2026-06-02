# Design Spec: Library + Editor Redesign

**Date:** 2026-06-02  
**Status:** Approved

---

## 1. Overview

Redesign the single-page interior AI app into a two-screen app (Editor + Library) with a shared asset library stored in `localStorage`. Users can build a tagged object library, select multiple assets, and generate multi-object room edits in one API call.

---

## 2. Navigation

- Header: Logo (left) · `Editor / Library` pill toggle (center) · model name (right)
- No sidebar, no page reload — JS toggles screen visibility (`display: none / block`)
- Active tab is orange-filled, inactive is ghost text

---

## 3. Library Screen

### 3.1 Upload & Tagging

- Upload button at top opens native file picker (JPEG/PNG/WebP)
- After picking a file, an **inline tag modal** appears over the library:
  - Shows image preview
  - **Room tag**: dropdown of presets (`Living Room`, `Bedroom`, `Kitchen`, `Bathroom`, `Dining Room`, `Office`, `Kids Room`, `Other`) + free-text input for custom
  - **Object tag**: dropdown of presets (`Sofa`, `Chair`, `Bed frame`, `Table`, `Lamp`, `Curtain`, `Rug`, `Mirror`, `Plant`, `Decor`, `Storage`, `Other`) + free-text input for custom
  - Save button → writes to `localStorage`
- All saved tags accumulate in `localStorage` so custom tags appear in future dropdowns

### 3.2 Asset Grid Layout

- Assets grouped by **room tag** — each group is a section with header + count badge
- Default view: 6 thumbs per section, collapsed
- **Expand button** per section → shows all assets in that section
- Thumbnail card: image + name + tag chips (room=green, object=blue)
- Selected state: orange border + checkmark overlay
- Hover: slight lift (`translateY(-2px)`) + border highlight

### 3.3 Selection & Navigation to Editor

- User selects up to 5 assets (clicking a 6th shows a toast: "Max 5 objects")
- **Floating bottom bar** appears when ≥1 selected:
  - Shows selected asset chips (thumbnail emoji + name)
  - Count indicator `X / 5`
  - Clear all button
  - **Generate → button** (orange, pulsing glow)
- Clicking **Generate →** switches to Editor screen with selections pre-loaded

---

## 4. Editor Screen

### 4.1 Layout

Two-column layout:
- **Left column**: Room Image upload + Subject Assets section
- **Right column**: Prompt panel (sticky, full height)

### 4.2 Room Image Upload

- Drag-and-drop zone + click-to-browse
- Preview thumbnail replaces zone after upload
- Click preview to replace

### 4.3 Subject Assets Section

**Toggle bar**: `⬆ Upload file` | `📚 Pick from Library`

**Upload mode**: single file picker, shows one preview

**Library mode**:
- Inline mini-grid of library assets, same grouping as Library screen
- Compact thumbnails with selection state
- Expand button per category
- Max 5 selections enforced with toast
- When arriving from Library screen, selections are pre-filled here

### 4.4 Prompt Panel (right column)

Top to bottom:

1. **Selected Objects chips** — removable, show object tag name + thumbnail emoji
2. **Action tags** — `Add to room` · `Replace existing` · `Remove` · `Restyle` (single-select)
3. **Style tags** — `Modern` · `Scandinavian` · `Industrial` · `Boho` · `Minimalist` · `Luxury` · `Japandi` · `Coastal` (multi-select)
4. **Placement tags** — `Left corner` · `Center wall` · `Right corner` · `Foreground` · `Background` (single-select)
5. **Prompt textarea** — auto-builds from tag selections, fully editable. Updates live as tags change but manual edits are not overwritten after first manual keystroke.
6. **Cost estimate note** — small, shows "~$0.06–0.10 per run"
7. **Generate Edit button** — full width, orange gradient, locked if no room image uploaded

---

## 5. Multi-Object API Architecture

The OpenAI images.edit API accepts at most 2 image inputs (room + 1 reference). To pass up to 5 objects in one call:

**Approach: Object grid composite**

Backend `main.py` receives up to 5 object images. Before calling the API it:
1. Opens all object images with Pillow
2. Arranges them in a 2-column grid (max 1024×1024 total composite)
3. Labels each cell: "Object 1", "Object 2", etc. drawn as small text overlays
4. Saves composite as single PNG, sends as the reference image

Prompt is automatically extended with:
```
OBJECT REFERENCE GUIDE: The second image is a grid of objects to place.
Object 1 (top-left): [tag], Object 2 (top-right): [tag], Object 3 (bottom-left): [tag] ...
Place each as described by the user directive.
```

Frontend sends: `room_image` + up to 5 `object_image_N` files + `prompt` + `object_tags` (JSON string of tag names in order).

Backend new endpoint signature:
```
POST /edit
  room_image:      UploadFile (required)
  prompt:          str (required)
  object_image_1:  UploadFile (optional)
  object_image_2:  UploadFile (optional)
  object_image_3:  UploadFile (optional)
  object_image_4:  UploadFile (optional)
  object_image_5:  UploadFile (optional)
  object_tags:     str (optional, JSON array of tag names)
```

---

## 6. localStorage Schema

```json
{
  "roomai_assets": [
    {
      "id": "uuid",
      "name": "Modern Sofa",
      "dataUrl": "data:image/png;base64,...",
      "roomTag": "Living Room",
      "objectTag": "Sofa",
      "createdAt": 1706000000
    }
  ],
  "roomai_custom_room_tags": ["Rooftop", "Balcony"],
  "roomai_custom_object_tags": ["Sculpture", "Fireplace"]
}
```

Each asset's image is stored as a base64 data URL. Custom tags are merged with presets in dropdowns.

---

## 7. Auto-Prompt Builder Logic

```
[Action] the [object1], [object2], [object3] into this room
in a [Style] style. Place them [Placement].
Maintain photorealism and match existing lighting and perspective.
```

- Builds on every tag click
- Locked from auto-rebuild after user manually edits textarea (flag: `userEditedPrompt = true`)
- Flag resets when selected objects change

---

## 8. State Flow

```
Library screen
  → user selects assets
  → clicks Generate →
  → JS sets selectedAssets[] in memory
  → switches to Editor screen
  → Editor reads selectedAssets[], pre-fills Subject Assets section
  → user uploads room image
  → clicks Generate Edit
  → POST /edit with room + object files + prompt
  → result shown as before/after
```

No URL routing needed — all state lives in JS variables + localStorage.

---

## 9. What Stays Unchanged

- Result display (before/after comparison, download button)
- `prepare_image()` pipeline in backend
- CORS, static file serving
- `.env` / `OPENAI_API_KEY` setup
- Cost estimate range

---

## 10. Out of Scope

- User accounts / cloud sync (localStorage only)
- Pagination (all assets load at once — fine for personal library)
- Drag-to-reorder assets
- Multi-room generation (one room image per run)
