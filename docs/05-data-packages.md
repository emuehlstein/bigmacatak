# ATAK Data Packages

Data packages let you push map markers, overlays, and reference data to connected ATAK clients.

## Creating Packages

ATAK data packages are ZIP files containing:
- `MANIFEST/manifest.xml` — package metadata
- KML/KMZ files — map markers and overlays
- Other files — images, PDFs, configs

### From KML/KMZ

If you have KML data (e.g., exported from Google Earth, QGIS, or CalTopo):

```bash
# Structure
mkdir -p my_package/MANIFEST

# Add your KML
cp markers.kml my_package/

# Create manifest
cat > my_package/MANIFEST/manifest.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<MissionPackageManifest version="2">
  <Configuration>
    <Parameter name="uid" value="my-markers-package"/>
    <Parameter name="name" value="My Markers"/>
  </Configuration>
  <Contents>
    <Content ignore="false" zipEntry="markers.kml"/>
  </Contents>
</MissionPackageManifest>
EOF

# Zip it
cd my_package && zip -r ../my_markers.zip . && cd ..
```

## Splitting Large KML Files

For complex KML files with many categories, split them into per-layer packages so ATAK operators can toggle each layer independently in Overlay Manager:

```python
#!/usr/bin/env python3
"""split_markers.py — Split KML into per-folder data packages."""
import os
import re
import zipfile
from xml.etree import ElementTree as ET

KML_NS = "{http://www.opengis.net/kml/2.2}"

def split_kml(kml_path, output_dir):
    tree = ET.parse(kml_path)
    root = tree.getroot()
    doc = root.find(f".//{KML_NS}Document")

    for folder in doc.findall(f"{KML_NS}Folder"):
        name = folder.find(f"{KML_NS}name").text
        safe_name = re.sub(r'[^\w\s-]', '', name).replace(' ', '_')
        pkg_name = f"SP_{safe_name}"

        # Build single-folder KML
        new_kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
        new_doc = ET.SubElement(new_kml, "Document")
        ET.SubElement(new_doc, "name").text = name

        # Copy styles
        for style in doc.findall(f"{KML_NS}Style") + doc.findall(f"{KML_NS}StyleMap"):
            new_doc.append(style)

        new_doc.append(folder)

        # Write data package
        kml_bytes = ET.tostring(new_kml, encoding="unicode", xml_declaration=True)
        manifest = f'''<?xml version="1.0" encoding="UTF-8"?>
<MissionPackageManifest version="2">
  <Configuration>
    <Parameter name="uid" value="{pkg_name}"/>
    <Parameter name="name" value="{name}"/>
  </Configuration>
  <Contents>
    <Content ignore="false" zipEntry="{pkg_name}.kml"/>
  </Contents>
</MissionPackageManifest>'''

        zip_path = os.path.join(output_dir, f"{pkg_name}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{pkg_name}.kml", kml_bytes)
            zf.writestr("MANIFEST/manifest.xml", manifest)

        print(f"Created: {zip_path}")

if __name__ == "__main__":
    import sys
    split_kml(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ".")
```

## Pushing to OTS

Use the OTS API to upload data packages:

```python
#!/usr/bin/env python3
"""push_to_ots.py — Push data packages to OpenTAK Server."""
import os
import sys
import glob
import requests

OTS_URL = os.environ.get("OTS_URL", "http://localhost:8081")
OTS_USER = os.environ.get("OTS_USER", "administrator")
OTS_PASS = os.environ.get("OTS_PASS", "password")

def push_package(zip_path):
    filename = os.path.basename(zip_path)
    session = requests.Session()
    session.auth = (OTS_USER, OTS_PASS)

    with open(zip_path, 'rb') as f:
        resp = session.post(
            f"{OTS_URL}/api/data_packages",
            files={"file": (filename, f, "application/zip")},
        )

    if resp.ok:
        print(f"✅ Pushed: {filename}")
    else:
        print(f"❌ Failed: {filename} — {resp.status_code} {resp.text}")

if __name__ == "__main__":
    for pattern in sys.argv[1:]:
        for path in glob.glob(pattern):
            push_package(path)
```

Usage:

```bash
# Push all packages
OTS_PASS=password python3 scripts/push_to_ots.py packages/*.zip

# Or push one
OTS_PASS=password python3 scripts/push_to_ots.py SP_Buildings.zip
```

## Managing Packages on OTS

```bash
# List packages
curl -s -u administrator:password http://localhost:8081/api/data_packages | python3 -m json.tool

# Delete a package
curl -X DELETE -u administrator:password http://localhost:8081/api/data_packages/{hash}
```

## ATAK Client

In ATAK, data packages appear under **Import Manager** and map overlays under **Overlay Manager**. Each split package shows as a separate toggleable layer.
