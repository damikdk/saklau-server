from file_operations import scan, thumb, import_scanned
import io
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from models import ImageFile, VideoFile
from config import DEFAULT_STATUS

app = FastAPI()
status = DEFAULT_STATUS

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

status = DEFAULT_STATUS


@app.get("/scan")
def scan_endpoint():
    return scan()


@app.get("/thumb")
def thumb_endpoint():
    return thumb()


@app.get("/import")
def import_files(background_tasks: BackgroundTasks):
    background_tasks.add_task(import_scanned)
    return {"message": "Notification sent in the background"}


@app.get("/")
def read_main():
    result_response = []

    all_images = ImageFile.select().order_by(ImageFile.taken_date.desc())
    # .limit(100)

    file: ImageFile
    for file in all_images:
        result_response.append({
            'path': file.path,
            'type': file.type,
            'width': file.width,
            'height': file.height,
            'status': file.status,
            'id': file.id,
            "taken_date": file.taken_date
        })

    all_videos = VideoFile.select().order_by(VideoFile.taken_date.desc())
    file: VideoFile
    for file in all_videos:
        result_response.append({
            'path': file.path,
            'type': file.type,
            'width': file.width,
            'height': file.height,
            'status': file.status,
            'id': file.id,
            "taken_date": file.taken_date
        })

    return result_response


@app.get("/file/{file_path:path}")
def read_file(file_path: str, request: Request):
    real_path = './' + file_path

    if not path.exists(real_path):
        print('There is no cache for this file:', real_path)
        return

    user_agent = request.headers.get("user-agent")
    if "Chrome" in user_agent and file_path.endswith("heic"):
        try:
            pil_image = Image.open(real_path)
            pil_image.thumbnail((3840, 2160), Image.Resampling.LANCZOS)
            img_io = io.BytesIO()
            pil_image.save(img_io, format="JPEG")
            img_io.seek(0)
            return StreamingResponse(io.BytesIO(img_io.read()), media_type="image/jpeg")
        except OSError as error:
            print('Error while HEIC conversion to png', error)

    return FileResponse(real_path)


@app.get("/status")
def get_tasks():
    return status
