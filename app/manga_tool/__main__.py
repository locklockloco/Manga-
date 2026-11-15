import argparse
import json
import sys
import threading
import webbrowser


def main():
    parser=argparse.ArgumentParser(description='Reconstruct manga illustrations at original pixel proportions; all automatic crops require review.')
    sub=parser.add_subparsers(dest='command')
    batch=sub.add_parser('process',help='Import one chapter and export reviewable proposals')
    batch.add_argument('input')
    batch.add_argument('-o','--output',required=True)
    batch.add_argument('--chapter',default='CH01')
    batch.add_argument('--include-all',action='store_true',help='Include all valid images, overriding relevance filters')
    batch.add_argument('--min-gutter',type=int,default=12,help='Minimum corridor height in original pixels (default 12; uncertain short/colored cuts require review)')
    batch.add_argument('--padding',type=int,default=4)
    batch.add_argument('--reading-direction',choices=['ltr','rtl'],default='ltr',help='Reading order of side-by-side panels; choose rtl for conventional manga')
    batch.add_argument('--no-vertical-splits',action='store_true',help='Keep side-by-side regions together')
    batch.add_argument('--preflight',action='store_true',help='Pause after import/index for reading-order review')
    batch.add_argument('--no-sheets',action='store_true',help='Defer thumbnail sheet generation')
    for name in ('pause','cancel','resume','status','qa','undo','redo','sync'):
        cmd=sub.add_parser(name,help=name+' saved chapter job')
        cmd.add_argument('output')
    review=sub.add_parser('review',help='Open local visual correction interface')
    review.add_argument('output',nargs='?')
    review.add_argument('--port',type=int,default=8765)
    review.add_argument('--no-browser',action='store_true')
    retry=sub.add_parser('reprocess',help='Reassess/export selected saved crop bounds only')
    retry.add_argument('output')
    retry.add_argument('--ids',nargs='+',required=True)
    args=parser.parse_args()
    try:
        if args.command in {'status','pause','cancel','qa','undo','redo','resume','sync'}:
            from .pipeline import job_status,job_control,refresh_qa,undo,redo,process_chapter
            if args.command=='sync':
                from .pipeline import sync_outputs,load_manifest
                result=sync_outputs(args.output,load_manifest(args.output))
            elif args.command=='status':result=job_status(args.output)
            elif args.command in {'pause','cancel'}:result=job_control(args.output,args.command)
            elif args.command=='qa':result=refresh_qa(args.output)
            elif args.command in {'undo','redo'}:result=(undo if args.command=='undo' else redo)(args.output)
            else:
                job=job_status(args.output)
                result=process_chapter(job['input'],args.output,chapter=job.get('chapter','CH01'),**job.get('options',{}))
            print(json.dumps(result,indent=2))
        elif args.command=='process':
            from .pipeline import process_chapter
            m=process_chapter(args.input,args.output,chapter=args.chapter,include_all=args.include_all,
                              min_gutter=args.min_gutter,padding=args.padding,vertical=not args.no_vertical_splits,reading_direction=args.reading_direction,preflight=args.preflight,generate_contact_sheets=not args.no_sheets)
            print(json.dumps({'panels':len(m['panels']),'status':m.get('production_status','REVIEW_REQUIRED'),'output':args.output,'warnings':m['warnings']},indent=2))
        elif args.command=='reprocess':
            from .pipeline import reprocess_panels
            reprocess_panels(args.output,args.ids)
            print('Selected saved bounds re-exported. Visual review required.')
        else:
            from .review import create_app
            port=getattr(args,'port',8765)
            url=f'http://127.0.0.1:{port}'
            if not getattr(args,'no_browser',False):
                threading.Timer(.8,lambda:webbrowser.open(url)).start()
            print(f'Local review: {url}',flush=True)
            create_app(getattr(args,'output',None)).run(host='127.0.0.1',port=port,debug=False,threaded=True)
    except (ValueError,OSError,RuntimeError) as e:
        parser.exit(1,f'Error: {e}\n')


if __name__=='__main__':
    main()
