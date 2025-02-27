from flask import Flask, render_template, send_file, request
from flask_socketio import SocketIO, emit
import yt_dlp
import os
import uuid
import time
from threading import Lock
import re
import shutil
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['DOWNLOAD_FOLDER'] = 'downloads'
socketio = SocketIO(app, cors_allowed_origins="*")

thread_lock = Lock()



def delete_downloads_folder():
    """Deletes the downloads folder and its contents."""
    DOWNLOADS_FOLDER = os.path.join(os.path.dirname(__file__), 'downloads')
    if os.path.exists(DOWNLOADS_FOLDER):
        print(f"[{datetime.now()}] Deleting downloads folder...")
        shutil.rmtree(DOWNLOADS_FOLDER)  # Recursively delete the folder and its contents
        print(f"[{datetime.now()}] Downloads folder deleted.")
    else:
        print(f"[{datetime.now()}] Downloads folder does not exist.")

    if not os.path.exists(app.config['DOWNLOAD_FOLDER']):
            os.makedirs(app.config['DOWNLOAD_FOLDER'])
    


scheduler = BackgroundScheduler()
# Schedule the task to run every day at midnight
scheduler.add_job(func=delete_downloads_folder, trigger="cron", hour=0, minute=0)
# scheduler.add_job(func=delete_downloads_folder, trigger="interval", seconds=5)
scheduler.start()


def sanitize_filename(filename):
    return "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_')).rstrip()

def progress_hook(d, socket_id, download_uuid):
    if d['status'] == 'downloading':
        # Clean ANSI codes and parse percentage
        percent_str = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_percent_str', '0%'))
        try:
            percent = float(percent_str.strip('%'))
        except:
            percent = 0
        
        # Clean other fields
        speed = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_speed_str', 'N/A')).strip()
        eta = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_eta_str', 'N/A')).strip()
        
        socketio.emit('progress', {
            'uuid': download_uuid,
            'percent': min(100, max(0, percent)),
            'speed': speed,
            'eta': eta
        }, room=socket_id)

@socketio.on('download_request')
def handle_download_request(data):
    download_uuid = str(uuid.uuid4())
    try:
        url = data['url']
        download_type = data['type']
        socket_id = request.sid
        
        download_dir = os.path.join(app.config['DOWNLOAD_FOLDER'])
        # os.makedirs(download_dir, exist_ok=True)

        emit('uuid', {'uuid': download_uuid}, room=socket_id)

        ydl_opts = {
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [lambda d: progress_hook(d, socket_id, download_uuid)],
            'quiet': True,
        }

        if data['type'] == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            sanitized_title = sanitize_filename(info['title'])
            
            actual_files = [f for f in os.listdir(download_dir)]
            if not actual_files:
                raise FileNotFoundError("Downloaded file not found")
            
            actual_filename = actual_files[0]
            file_path = os.path.join(download_dir, actual_filename)

            timeout = time.time() + 30
            while not os.path.exists(file_path):
                if time.time() > timeout:
                    raise TimeoutError("File write timeout")
                time.sleep(0.1)

            emit('download_ready', {
                'uuid': download_uuid,
                'filename': actual_filename
            }, room=socket_id)

    except Exception as e:
        emit('error', {'uuid': download_uuid, 'message': str(e)})
        cleanup_directory(download_uuid)

def cleanup_directory(uuid):
    dir_path = os.path.join(app.config['DOWNLOAD_FOLDER'], uuid)
    if os.path.exists(dir_path):
        for f in os.listdir(dir_path):
            os.remove(os.path.join(dir_path, f))
        os.rmdir(dir_path)

@app.route('/download/<uuid>/<filename>')
def download_file(uuid, filename):
    file_path = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
    try:
        return send_file(file_path, as_attachment=True)
    finally:
        cleanup_directory(uuid)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    try:
        if not os.path.exists(app.config['DOWNLOAD_FOLDER']):
            os.makedirs(app.config['DOWNLOAD_FOLDER'])
        socketio.run(app, debug=True)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()  # Shut down the scheduler when the app stops