import io
from fastapi import BackgroundTasks, FastAPI, Request

from PIL import Image
from pillow_heif import register_heif_opener
from PIL.ExifTags import TAGS, GPSTAGS

import datetime

from peewee import SqliteDatabase, Model, CharField, UUIDField, FloatField, DateTimeField, IntegerField
from os import walk, path, makedirs
import time
from enum import StrEnum

from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

import ffmpeg
import imagehash
import hashlib
import uuid

import mimetypes
mimetypes.init()
register_heif_opener()


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = SqliteDatabase("data.db")

THUMBNAIL_SIZE = (256, 256)
THUMB_FOLDER_NAME = 'cache'
THUMB_FORMAT = 'jpg'
HERE_PATH = '.'

DEFAULT_STATUS = 'nothing new'
status = DEFAULT_STATUS


class FileStatus(StrEnum):
    SCANNED = 'SCANNED'
    IMPORTED = 'IMPORTED'


class File(Model):
    id = UUIDField(primary_key=True, unique=True, default=uuid.uuid4)
    path = CharField(default="", max_length=200)
    file_size = IntegerField(default=0)
    hash = CharField(default="", max_length=64)
    created_date = DateTimeField(default=datetime.datetime.now)
    added_date = DateTimeField(default=datetime.datetime.now)
    status = CharField(default="", max_length=20)
    type = CharField(default="", max_length=20)

    class Meta:
        database = db


class ImageFile(File):
    height = IntegerField(default=0)
    width = IntegerField(default=0)
    phash = CharField(default="", max_length=64)
    taken_date = DateTimeField(default=datetime.datetime.min)
    geo = CharField(default="", max_length=64)


class VideoFile(File):
    duration = FloatField(default=0)
    codec = CharField(default="")


class TextFile(File):
    character_count = IntegerField(0)
    language = CharField(default="")


@app.get("/scan")
def scan():
    global status
    new_items = []
    start_scan = time.time()
    status = 'Scanning...'

    for dirpath, dirnames, filenames in walk(HERE_PATH):
        if dirpath.startswith('./' + THUMB_FOLDER_NAME):
            continue

        for filename in filenames:
            file_path = path.join(dirpath, filename)

            if not is_media_file(file_path) or not is_file_valid(file_path):
                print('SKIPPED bad file:', file_path)
                continue

            if File.select().where(File.path == file_path):
                print('SKIPPED already imported file:', file_path)
                continue

            file_size = path.getsize(file_path)
            file_type = get_mimetype(file_path)

            File.create(path=file_path, type=file_type,
                        file_size=file_size, status=FileStatus.SCANNED)

            match file_type:
                case 'image':
                    ImageFile.create(path=file_path, type=file_type,
                                     file_size=file_size, status=FileStatus.SCANNED)
                case 'video':
                    VideoFile.create(path=file_path, type=file_type,
                                     file_size=file_size, status=FileStatus.SCANNED)
                case 'text':
                    TextFile.create(path=file_path, type=file_type,
                                    file_size=file_size, status=FileStatus.SCANNED)
                case _:
                    print("Not media file")

            new_items.append(file_path)
            print('New file scanned:', file_path)
            status = 'Scanning... ' + str(len(new_items)) + ' new files'

    total_time = round(time.time() - start_scan, 2)
    finish_string = 'Scan of ' + \
        str(len(new_items)) + ' is over in ' + str(total_time)
    print(finish_string)
    status = finish_string

    return new_items


@app.get("/thumb")
def sync():
    global status

    start_thumb = time.time()

    all_images = ImageFile.select()
    all_count = len(all_images)
    thumbnailed_count = 0

    status = 'Thumbnailing...'

    if not path.exists(THUMB_FOLDER_NAME):
        makedirs(THUMB_FOLDER_NAME)

    for image_db in all_images:
        try:
            thumbnail_path = path.join(
                THUMB_FOLDER_NAME, str(image_db.id)) + '.jpg'

            if path.exists(thumbnail_path):
                # print('Already cached:', file_path)
                continue

            with Image.open(image_db.path) as originFile:
                originFile.thumbnail(THUMBNAIL_SIZE)
                originFile.save(thumbnail_path)
        except OSError as error:
            print("Cannot create thumbnail for", thumbnail_path, error)

        thumbnailed_count += 1
        status = 'Thumbnailing...' + \
            str(thumbnailed_count) + '/' + str(all_count)

    total_time = round(time.time() - start_thumb, 2)

    finish_string = 'Thumbnailing of ' + \
        str(thumbnailed_count) + ' is over in ' + str(total_time)
    print(finish_string)
    status = finish_string


def import_scanned():
    global status

    start_import = time.time()

    all_images = ImageFile.select().where(ImageFile.status == FileStatus.SCANNED)
    all_count = len(all_images)
    imported_count = 0

    status = 'Importing...'

    for image_db in all_images:
        imported_count += 1

        try:
            shaHash = sha256sum(image_db.path)
            image_db.hash = shaHash
        except OSError:
            print("Cannot update shaHash for", image_db.path)

        try:
            image = Image.open(image_db.path)

            image_db.width, image_db.height = image.size

            exif_raw = image.getexif()
            exif = {TAGS.get(k, k): v for k, v in exif_raw.items()}

            if 'DateTime' in exif:
                date = datetime.datetime.strptime(
                    exif['DateTime'], '%Y:%m:%d %H:%M:%S')
                image_db.taken_date = date

            if 'GPSInfo' in exif:
                image_db.geo = get_coordinates(get_geo(exif_raw))

            image_db.phash = imagehash.average_hash(image)
        except OSError:
            print("Cannot get meta for", image_db.path)

        image_db.status = FileStatus.IMPORTED
        image_db.save()
        print('Image imported:', image_db.path, imported_count, '/', all_count)

        status = 'Importing...' + str(imported_count) + '/' + str(all_count)

    total_time = round(time.time() - start_import, 2)

    finish_string = 'Import of ' + \
        str(imported_count) + ' is over in ' + str(total_time)
    print(finish_string)
    status = finish_string


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
            'id': file.id
        })

    return result_response


def is_file_valid(filename: str):
    return not filename.startswith('./cache') and not filename.endswith(".DS_Store")


def is_media_file(fileName: str):
    mime = get_mimetype(fileName)

    if mime in ['audio', 'video', 'image']:
        return True

    return False


def get_mimetype(fileName: str):
    mimestart = mimetypes.guess_type(fileName)[0]

    if mimestart != None:
        mimestart = mimestart.split('/')[0]
        return mimestart


def sha256sum(file_path):
    with open(file_path, 'rb', buffering=0) as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


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


db.create_tables([File, ImageFile, VideoFile, TextFile], safe=True)


def get_geo(exif):
    for key, value in TAGS.items():
        if value == "GPSInfo":
            break
    gps_info = exif.get_ifd(key)
    return {
        GPSTAGS.get(key, key): value
        for key, value in gps_info.items()
    }


def get_decimal_from_dms(dms, ref):
    degrees = dms[0]
    minutes = dms[1] / 60.0
    seconds = dms[2] / 3600.0

    if ref in ['S', 'W']:
        degrees = -degrees
        minutes = -minutes
        seconds = -seconds

    return round(degrees + minutes + seconds, 5)


def get_coordinates(geotags):
    lat = get_decimal_from_dms(
        geotags['GPSLatitude'], geotags['GPSLatitudeRef'])

    lon = get_decimal_from_dms(
        geotags['GPSLongitude'], geotags['GPSLongitudeRef'])

    return (lat, lon)
