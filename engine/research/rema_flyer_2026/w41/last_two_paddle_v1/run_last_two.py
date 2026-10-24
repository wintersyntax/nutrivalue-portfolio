#!/usr/bin/env python3
"""W41 targeted local PaddleOCR on four existing title-guard crops.

Never infers price cents from manual labels. Does not call Tjek/Vision/LLM/DB.
PaddleOCR may check model hosting/download weights if its model cache is empty.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping

WEEK = '2026-W41'
CROPS = {
    'p02-couscous': {
        'price': ('p02-w41-p02-n001-price-review.png', '4f6a98a81c988cfa2078db78b1bbe875346f5ab343e338023a55d72be99b7198'),
        'advert': ('p02-w41-p02-n001-advert-review.png', 'c36870db742114623fcc3df7d3d9d1e83df108416b66f6074dbbe4e09afbb3ac'),
    },
    'p16-egg6pk': {
        'price': ('p16-w41-p16-n001-price-review.png', '69ebca190e62096a6855569a7863114560f08231a27a2ed8e7be88d119d11d07'),
        'advert': ('p16-w41-p16-n001-advert-review.png', '4b2a30e2702da762c289ae0cdb6593c083e3280eb0db91a65b0f0e806a0db75a'),
    },
}


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as fp:
        for buf in iter(lambda: fp.read(1024*1024), b''):
            h.update(buf)
    return h.hexdigest()


def check_images(kit_dir):
    files=[]
    for issue, modes in CROPS.items():
        for mode, (name, expected) in modes.items():
            file=kit_dir/name
            if not file.is_file() or file.is_symlink():
                raise FileNotFoundError(f'Missing or unsafe image: {file}')
            if not 1000 < file.stat().st_size < 8_000_000:
                raise ValueError(f'Implausible image size: {file}')
            if file.open('rb').read(8) != b'\x89PNG\r\n\x1a\n':
                raise ValueError(f'Not a PNG: {file}')
            actual=sha(file)
            if actual != expected:
                raise ValueError(f'Image SHA mismatch: {file}, sha256={actual}')
            files.append((issue,mode,file,actual))
    return files


def jsonable(obj):
    if obj is None or isinstance(obj, (str,int,float,bool)):
        return obj
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k,v in obj.items()}
    if hasattr(obj,'tolist'):
        return jsonable(obj.tolist())
    if isinstance(obj, (list,tuple)):
        return [jsonable(v) for v in obj]
    return str(obj)


def get_lines(result):
    root=getattr(result,'json',None)
    if callable(root):root=root()
    if root is None and isinstance(result,Mapping):root=result
    if isinstance(root,str):root=json.loads(root)
    if not isinstance(root,Mapping):
        raise ValueError('Unexpected PaddleOCR response structure')
    data=root.get('res',root)
    if not isinstance(data,Mapping):
        raise ValueError('Missing OCR result data')
    texts=jsonable(data.get('rec_texts',[]))
    scores=jsonable(data.get('rec_scores',[]))
    boxes=data.get('rec_boxes')
    if boxes is None:boxes=data.get('rec_polys',[])
    boxes=jsonable(boxes)
    if not isinstance(texts,list) or not isinstance(scores,list) or not isinstance(boxes,list):
        raise ValueError('Unexpected OCR list data')
    return [{'text':str(t),'confidence':scores[i] if i<len(scores) else None,
             'box':boxes[i] if i<len(boxes) else None} for i,t in enumerate(texts)]


def model_instance():
    from paddleocr import PaddleOCR
    return PaddleOCR(ocr_version='PP-OCRv5',engine='onnxruntime',device='cpu',
                     use_doc_orientation_classify=False,use_doc_unwarping=False,
                     use_textline_orientation=False)


def run(files, out, model):
    if out.exists():
        raise FileExistsError(f'Output exists; choose new directory: {out}')
    out.mkdir(parents=True)
    data={'schema':'nutrivalue_w41_last_two_paddle_review_only_v1','week':WEEK,
          'generated_at_utc':datetime.now(timezone.utc).isoformat(),
          'review_only':True,'no_database_writes':True,
          'ocr_not_ground_truth':True,'image_count':len(files),'crops':[]}
    audit=['# W41 — two unresolved ads: PaddleOCR evidence (REVIEW ONLY)','',
           'OCR text and coordinates are raw evidence, not confirmed offer prices.','']
    for index,(issue,mode,file,digest) in enumerate(files,1):
        res=list(model.predict(str(file)))
        if len(res)!=1:
            raise ValueError(f'Expected one result for {file.name}; received {len(res)}')
        lines=get_lines(res[0])
        data['crops'].append({'issue':issue,'mode':mode,'file':file.name,
                              'image_sha256':digest,'lines':lines})
        audit += [f'## {issue} — {mode}',f'Lines: {len(lines)}','']
        audit += [f'- {l["text"]} | confidence={l["confidence"]} | box={l["box"]}' for l in lines]
        audit.append('')
        (out/'results.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (out/'audit.md').write_text('\n'.join(audit)+'\n',encoding='utf-8')
        print(f'[{index}/{len(files)}] {issue} {mode}: {len(lines)} lines',flush=True)
    print('Finished: NO accepted prices / NO DB writes; report:', out/'audit.md')
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kit-dir',type=Path,required=True)
    p.add_argument('--out-dir',type=Path)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--check',action='store_true')
    group.add_argument('--run',action='store_true')
    a=p.parse_args()
    files=check_images(a.kit_dir)
    print('Verified original W41 images:',len(files))
    if a.check:
        print('PASS: no Paddle model, HTTP or database calls')
        return
    if a.out_dir is None:p.error('--out-dir is required with --run')
    if a.out_dir.exists():p.error('Output directory exists; use new timestamp')
    run(files,a.out_dir,model_instance())


if __name__=='__main__':main()
