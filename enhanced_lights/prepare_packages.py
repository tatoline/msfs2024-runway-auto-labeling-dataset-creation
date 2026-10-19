"""Prepare short-path standalone MSFS light-package projects, one per airport."""
import argparse
import copy
from pathlib import Path
import xml.etree.ElementTree as ET


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--scene',required=True,help='combined XML written by the light generator')
    ap.add_argument('--output-dir',required=True,help='fresh short path, e.g. C:\\msfs_light_build')
    args=ap.parse_args();out=Path(args.output_dir).resolve()
    if out.exists(): ap.error('output already exists; use a fresh short path')
    scene=ET.parse(args.scene).getroot();airports=scene.findall('Airport')
    if not airports: ap.error('scene has no Airport records')
    ids=[a.get('ident','') for a in airports]
    if len(ids)!=len(set(ids)) or any(len(v)!=4 or not v.isalnum() for v in ids): ap.error('invalid airport identifiers')
    for airport in airports:
        icao=airport.get('ident');code=icao.lower();package=f'taylan-{code}-enhanced-lights'
        root=out/icao
        expected=root/'Packages'/package/'scenery'/'taylan'/code/(code+'-lights.bgl')
        if len(str(expected))>=230: ap.error('output path is too long for the SDK; choose a shorter root')
    for airport in airports:
        icao=airport.get('ident');code=icao.lower();package=f'taylan-{code}-enhanced-lights'
        root=out/icao;source=root/'PackageSources'/'scene';definition=root/'PackageDefinitions'
        source.mkdir(parents=True);definition.mkdir()
        single=ET.Element('FSData',dict(scene.attrib));single.append(copy.deepcopy(airport))
        ET.indent(single,space='  ')
        ET.ElementTree(single).write(source/(code+'-lights.xml'),encoding='utf-8',xml_declaration=True)
        (root/(icao+'_Enhanced_Lights.xml')).write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<Project Version="2" Name="{icao} Enhanced Runway Lights" FolderName="Packages" MetadataFolderName="PackagesMetadata">
  <OutputDirectory>.</OutputDirectory><TemporaryOutputDirectory>_PackageInt</TemporaryOutputDirectory>
  <Packages><Package>PackageDefinitions\\{package}.xml</Package></Packages>
</Project>\n''',encoding='utf-8')
        (definition/(package+'.xml')).write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<AssetPackage Version="1.0.0">
  <ItemSettings><ContentType>SCENERY</ContentType><Title>{icao} Enhanced Runway Lights</Title><Manufacturer>Taylan</Manufacturer><Creator>Taylan</Creator><Description>Additive runway visibility experiment.</Description></ItemSettings>
  <Flags><VisibleInStore>false</VisibleInStore><CanBeReferenced>false</CanBeReferenced></Flags>
  <PackageOrderHint>CUSTOM_LOCAL_SCENERY</PackageOrderHint>
  <AssetGroups><AssetGroup Name="{code}-lights"><Type>BGL</Type><Flags><FSXCompatibility>false</FSXCompatibility></Flags><AssetDir>PackageSources\\scene</AssetDir><OutputDir>scenery\\taylan\\{code}</OutputDir></AssetGroup></AssetGroups>
</AssetPackage>\n''',encoding='utf-8')
        print(root/(icao+'_Enhanced_Lights.xml'))
    print('Projects prepared only. Fully close MSFS before building; see docs/enhanced-lights.md.')


if __name__=='__main__': main()
