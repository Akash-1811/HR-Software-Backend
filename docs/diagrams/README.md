# Diagram exports (PNG / SVG)

The `.mmd` files here are plain **Mermaid** text. Use any method below to get a downloadable image.

## Option A — Mermaid Live (fastest)

1. Open **[mermaid.live](https://mermaid.live)**.
2. Delete the default diagram in the editor.
3. Open `attenova_software_flow.mmd` or `attenova_er.mmd` in this folder, copy **all** text (`%%` lines are comments and may be omitted).
4. Paste into the left panel. The diagram renders on the right.
5. Click **Actions** (or the download icon) → **PNG** or **SVG** to download.

Repeat for the second diagram if you need both.

## Option B — VS Code / Cursor

Install a Mermaid preview extension, open the `.mmd` file, and use the extension’s export if available.

## Option C — CLI (developers)

If [Mermaid CLI](https://github.com/mermaid-js/mermaid-cli) is installed:

```bash
mmdc -i attenova_software_flow.mmd -o attenova_software_flow.svg
mmdc -i attenova_er.mmd -o attenova_er.svg
```

PNG: add `-w 2400` (width) as needed.

## Combined view

For narrative + all diagrams in one place, see **[../DIAGRAMS.md](../DIAGRAMS.md)** in the parent `docs` folder.
