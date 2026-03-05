"""
Simple server that serves the triage dashboard and provides a refresh endpoint
to sync data from Cosmos DB.
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import subprocess
import json
import sys
import os

# ── Auto-load .env from script directory ──────────────────────────────────────
# This means the server works correctly whether started via start_server.bat,
# PowerShell, or VS Code — no manual env var setup needed.
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_file):
    with open(_env_file, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# Change to the Mock Screens root (parent of this script's directory) so static files
# are served from the same root as the VS Code http.server task.
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))

class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Handle refresh API endpoint
        if self.path == '/api/refresh':
            self.handle_refresh()
        else:
            # Serve static files normally
            super().do_GET()
    
    def do_POST(self):
        if self.path == '/api/refresh':
            self.handle_refresh()
        else:
            self.send_error(404, "Not Found")
    
    def handle_refresh(self):
        """Run export_qa_triage.py to refresh data from Cosmos DB, return fresh data inline."""
        try:
            self.log_message("Starting data refresh from Cosmos DB...")

            # Get the Python executable from the virtual environment
            venv_python = os.path.join('..', '.venv', 'Scripts', 'python.exe')
            if not os.path.exists(venv_python):
                venv_python = sys.executable

            # Run the QA triage export script
            result = subprocess.run(
                [venv_python, os.path.join(SCRIPT_DIR, 'export_qa_triage.py')],
                capture_output=True,
                text=True,
                cwd=SCRIPT_DIR,
                timeout=180
            )

            data_file = os.path.join(SCRIPT_DIR, 'qa_triage_data.json')

            if result.returncode == 0:
                # Script succeeded — read the freshly written JSON and return it
                with open(data_file, 'r', encoding='utf-8') as f:
                    fresh_data = json.load(f)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'no_data': False,
                    'message': 'Data refreshed from Cosmos DB',
                    'data': fresh_data
                }).encode())
                self.log_message("Data refresh completed — returned %d documents", len(fresh_data.get('documents', [])))

            elif result.returncode == 2:
                # Exit code 2 = Cosmos has 0 TRCs. Return existing cached data.
                fresh_data = None
                if os.path.exists(data_file):
                    with open(data_file, 'r', encoding='utf-8') as f:
                        fresh_data = json.load(f)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'no_data': True,
                    'message': 'No TRC records in QA Cosmos yet — showing cached data.',
                    'data': fresh_data
                }).encode())
                self.log_message("No TRC data in Cosmos, returning cached JSON")
                self.log_message("Data refresh completed successfully")
            elif result.returncode == 3:
                # Exit code 3 = Cosmos credentials not configured. Return cached data silently.
                fresh_data = None
                if os.path.exists(data_file):
                    with open(data_file, 'r', encoding='utf-8') as f:
                        fresh_data = json.load(f)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'not_configured': True,
                    'message': 'Cosmos credentials not set — showing cached data. Fill in .env to enable live refresh.',
                    'data': fresh_data
                }).encode())
                self.log_message("Cosmos credentials not configured, returning cached JSON")
            else:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                response = {
                    'success': False,
                    'message': 'Script execution failed',
                    'error': result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
                }
                self.wfile.write(json.dumps(response).encode())
                self.log_message(f"Data refresh failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            self.send_response(504)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {'success': False, 'message': 'Script timed out after 120 seconds'}
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {'success': False, 'message': str(e)}
            self.wfile.write(json.dumps(response).encode())
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


def run_server(port=5502):
    server_address = ('', port)
    httpd = HTTPServer(server_address, DashboardHandler)
    print(f"=" * 50)
    print(f"TCA Triage Dashboard Server")
    print(f"=" * 50)
    print(f"Dashboard: http://localhost:{port}/Triaging-Dashboard/qa_triage_dashboard.html")
    print(f"Refresh API: http://localhost:{port}/api/refresh")
    print(f"=" * 50)
    print("Press Ctrl+C to stop the server")
    print()
    httpd.serve_forever()


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5500
    run_server(port)
