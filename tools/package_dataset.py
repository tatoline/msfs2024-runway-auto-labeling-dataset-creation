"""Validate a reviewed release and write a ZIP plus SHA-256 checksum."""
import argparse
from pathlib import Path
import zipfile
from validate_dataset import digest, validate


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset-dir',required=True)
    ap.add_argument('--output',required=True,help='new .zip outside the dataset directory')
    args=ap.parse_args();root=Path(args.dataset_dir).resolve();output=Path(args.output).resolve()
    if output.exists() or output.with_suffix('.zip.sha256').exists(): ap.error('archive/checksum exists')
    if output.is_relative_to(root) or output.suffix.lower()!='.zip': ap.error('choose a .zip outside the source dataset')
    try: result=validate(root)
    except (OSError,ValueError,KeyError,TypeError) as error: ap.error(str(error))
    if result['kind']!='reviewed_release': ap.error('export a reviewed release before packaging')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        for file in sorted(root.rglob('*')):
            if file.is_file(): archive.write(file,Path(root.name)/file.relative_to(root))
    output.with_suffix('.zip.sha256').write_text(digest(output)+'  '+output.name+'\n',encoding='utf-8')
    print(f'Packaged {result["images"]} images -> {output}')


if __name__=='__main__': main()
