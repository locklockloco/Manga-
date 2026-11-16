"""Run Tk on its own main thread/process (required by macOS Cocoa)."""
import json
import sys


def main():
    try:
        import tkinter as tk
        from tkinter import filedialog
        kind=sys.argv[1]
        root=tk.Tk()
        root.withdraw()
        root.attributes('-topmost',True)
        try:
            if kind=='file':
                path=filedialog.askopenfilename(title='Select chapter archive or image',filetypes=[('Chapter archives and images','*.zip *.webarchive *.jpg *.jpeg *.png *.webp'),('All files','*')])
            else:
                path=filedialog.askdirectory(title='Select '+kind+' folder')
        finally:
            root.destroy()
        print(json.dumps({'path':path}))
    except Exception as exc:
        print(json.dumps({'error':str(exc)}))
        sys.exit(1)


if __name__=='__main__':
    main()
