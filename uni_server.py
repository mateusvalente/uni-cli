"""Painel local e ferramentas de assets do uni-cli."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uni_build import build_routes, BuildBusy

ROOT = Path.cwd().resolve()


def run(*command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=240)
    if result.returncode: raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout


def build():
    build_routes(ROOT)
    return json.loads((ROOT / 'storage/framework/manifest/compiler-status.json').read_text())


def asset_file(action, filename):
    if action not in ('minify', 'unminify'):
        raise ValueError('Acao invalida.')
    source = (ROOT / filename).resolve()
    if not source.is_relative_to(ROOT) or source.suffix.lower() not in ('.js', '.css') or not source.is_file():
        raise ValueError('Informe um arquivo JS ou CSS existente dentro do projeto.')
    stem = source.stem.removesuffix('.min').removesuffix('.formatted')
    target = source.with_name(stem + ('.min' if action == 'minify' else '.formatted') + source.suffix)
    if target.resolve() == source or not target.resolve().is_relative_to(ROOT):
        raise ValueError('O destino nao pode ser o arquivo de entrada nem sair do projeto.')
    code = source.read_text(encoding='utf-8-sig')
    if action == 'minify':
        php = r'''require $argv[1] . '/vendor/autoload.php';
echo (new FrontendCore\Assets\AssetMinifier())->minify(file_get_contents($argv[2]), $argv[3]);'''
        output = run('php', '-r', php, str(ROOT), str(source), source.suffix[1:].lower())
    else:
        import cssbeautifier
        import jsbeautifier
        formatter = cssbeautifier if source.suffix.lower() == '.css' else jsbeautifier
        output = formatter.beautify(code) + '\n'
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as temporary:
        temporary.write(output)
        temp_path = Path(temporary.name)
    try:
        temp_path.chmod(0o644)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
    return {'ok': True, 'action': action, 'input': source.relative_to(ROOT).as_posix(),
            'output': target.relative_to(ROOT).as_posix(), 'bytes_before': source.stat().st_size,
            'bytes_after': target.stat().st_size}


PAGE = '''<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compilador · Institucional</title>
<style>body{font:16px system-ui;max-width:760px;margin:60px auto;padding:24px;background:#f5f2ec;color:#0c1147}button{padding:14px 24px;border:0;border-radius:8px;background:#044aad;color:white;cursor:pointer}button:disabled{opacity:.5}pre{white-space:pre-wrap;padding:20px;background:white;border-radius:8px}a{color:#044aad}</style>
<h1>Compilador do Institucional</h1><p>Atualize rotas, templates, componentes e arquivos CSS/JS após editar o projeto.</p>
<button id="build" type="button">Pré-compilar projeto</button> <a href="/">Abrir site</a>
<pre id="result" role="status" aria-live="polite">Consultando último build…</pre>
<h2>Arquivo CSS ou JavaScript</h2>
<form id="asset-form">
<label for="asset-path">Caminho relativo ao projeto</label>
<input id="asset-path" required placeholder="public/assets/global/js/tradutorGoogle.js" style="width:100%;box-sizing:border-box;padding:12px;margin:12px 0">
<select id="asset-action" aria-label="Operacao"><option value="minify">Minificar</option><option value="unminify">Desminificar (formatar)</option></select>
<button type="submit">Processar arquivo</button>
</form>
<p>A formatacao nao recupera comentarios removidos. Saida: .min ou .formatted, sem alterar o original.</p>
<script>
const button = document.querySelector('#build'), result = document.querySelector('#result');
async function status() { try { result.textContent = JSON.stringify(await (await fetch('/__compiler/status')).json(), null, 2); } catch(e) { result.textContent=e.message; } }
button.addEventListener('click', async () => {
 button.disabled=true; result.textContent='Compilando…';
 try { const response=await fetch('/__compiler/build', {method:'POST', headers:{'X-Compiler-Request':'1'}}); const data=await response.json(); result.textContent=JSON.stringify(data,null,2); }
 catch(e) { result.textContent=e.message; } finally { button.disabled=false; }
}); status();
document.querySelector('#asset-form').addEventListener('submit', async event => {
 event.preventDefault(); const submit=event.target.querySelector('button'); submit.disabled=true;
 try { const response=await fetch('/__compiler/asset', {method:'POST', headers:{'X-Compiler-Request':'1','Content-Type':'application/json'}, body:JSON.stringify({action:document.querySelector('#asset-action').value,path:document.querySelector('#asset-path').value})}); result.textContent=JSON.stringify(await response.json(),null,2); }
 catch(e) { result.textContent=e.message; } finally { submit.disabled=false; }
});
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data, html=False):
        payload = (data if html else json.dumps(data, ensure_ascii=False)).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ('text/html' if html else 'application/json') + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path in ('/__compiler', '/__compiler/'):
            self.respond(200, PAGE, html=True)
        elif self.path == '/__compiler/status':
            status = ROOT / 'storage/framework/manifest/compiler-status.json'
            self.respond(200, json.loads(status.read_text()) if status.is_file() else {'ok': True, 'compiled': False})
        else:
            self.respond(404, {'ok': False, 'error': 'Rota nao encontrada.'})

    def do_POST(self):
        if self.path not in ('/__compiler/build', '/__compiler/asset'):
            return self.respond(404, {'ok': False, 'error': 'Rota nao encontrada.'})
        origin = self.headers.get('Origin')
        if self.headers.get('X-Compiler-Request') != '1' or (
            origin and urlsplit(origin).netloc != self.headers.get('Host')
        ):
            return self.respond(403, {'ok': False, 'error': 'Use o botao local ou X-Compiler-Request: 1.'})
        try:
            if self.path == '/__compiler/asset':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    return self.respond(400, {'ok': False, 'error': 'Corpo JSON ausente ou muito grande.'})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or not isinstance(data.get('path'), str):
                    raise ValueError('Informe action e path.')
                self.respond(200, asset_file(data.get('action'), data['path']))
            else:
                self.respond(200, build())
        except ValueError as error:
            self.respond(400, {'ok': False, 'error': str(error)})
        except BuildBusy as error:
            self.respond(409, {'ok': False, 'error': str(error)})
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            self.respond(500, {'ok': False, 'error': str(error)})



def serve(root):
    global ROOT
    ROOT = Path(root).resolve()
    print('Painel uni-cli na porta interna 9001.', flush=True)
    ThreadingHTTPServer(('0.0.0.0', 9001), Handler).serve_forever()
