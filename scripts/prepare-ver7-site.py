"""Apply development-only overlay to a disposable Sites checkout; never push/deploy."""
import argparse, shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('checkout',type=Path);a=p.parse_args()
root=Path(__file__).resolve().parents[1];dest=a.checkout.resolve()
if dest==root:raise SystemExit('Use a separate Sites development checkout')
index=dest/'dist/index.html';text=index.read_text()
for name in ('strategy-v7.js','strategy-v7-app.js'):
    shutil.copy2(root/'site-overlay/dist'/name,dest/'dist'/name)
marker='<script>window.VER7_DEVELOPMENT=true;</script><script src="strategy-v7.js"></script><script src="strategy-v7-app.js"></script>'
if marker not in text:text=text.replace('</body>',marker+'</body>')
index.write_text(text)
print('Development overlay installed. Production deployment remains disabled.')
