import io
from fastapi import BackgroundTasks, FastAPI, Request

from PIL import Image
from pillow_heif import register_heif_opener
from PIL.ExifTags import TAGS, GPSTAGS

import datetime
import math

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
THUMB_FOLDER = 'cache'
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
    height = IntegerField(default=0)
    width = IntegerField(default=0)
    taken_date = DateTimeField(default=datetime.datetime.min)


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
        if dirpath.startswith('./' + THUMB_FOLDER):
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

            new_file_record = File.create(path=file_path, type=file_type,
                                          file_size=file_size, status=FileStatus.SCANNED)

            match file_type:
                case 'image':
                    ImageFile.create(path=file_path, type=file_type,
                                     file_size=file_size, status=FileStatus.SCANNED, id=new_file_record.id)
                case 'video':
                    VideoFile.create(path=file_path, type=file_type,
                                     file_size=file_size, status=FileStatus.SCANNED, id=new_file_record.id)
                case 'text':
                    TextFile.create(path=file_path, type=file_type,
                                    file_size=file_size, status=FileStatus.SCANNED, id=new_file_record.id)
                case _:
                    print("Not media file")

            new_items.append(file_path)
            print('New file scanned:', file_path)
            status = 'Scanning... ' + str(len(new_items)) + ' new files'

    total_time = round(time.time() - start_scan, 2)
    finish_string = f'Last scan: {total_time} secs ({len(new_items)} files)'
    print(finish_string)
    status = finish_string

    return new_items


@app.get("/thumb")
def thumb():
    global status

    start_thumb = time.time()

    all_images = ImageFile.select()
    all_videos = VideoFile.select()
    # .where(VideoFile.status == FileStatus.SCANNED)

    all_count = len(all_images) + len(all_videos)
    thumbed_count = 0

    status = 'Thumbnailing...'

    if not path.exists(THUMB_FOLDER):
        makedirs(THUMB_FOLDER)

    for image_db in all_images:
        thumb_path = path.join(THUMB_FOLDER, str(image_db.id)) + '.jpg'

        if path.exists(thumb_path):
            # print('Already cached:', file_path)
            continue

        try:

            with Image.open(image_db.path) as originFile:
                originFile.thumbnail(THUMBNAIL_SIZE)
                originFile.save(thumb_path)
        except OSError as error:
            print("Cannot create thumbnail for", thumb_path, error)

        thumbed_count += 1
        status = f'Thumbnailing: {thumbed_count} / {all_count}'

    for video_db in all_videos:
        thumb_path = path.join(THUMB_FOLDER, str(video_db.id)) + '.webp'

        if path.exists(thumb_path):
            # print('Already cached:', file_path)
            continue

        ffmpeg.input(video_db.path).output(thumb_path,
                                           vcodec='libwebp',
                                           vf="fps=fps=10,scale='min(256,iw)':min'(256,ih)':force_original_aspect_ratio=decrease",
                                           lossless=1,
                                           loop=0,
                                           fps_mode="passthrough",
                                           preset='default',
                                           format='webp',
                                           loglevel="quiet",
                                           t=2).run()

        thumbed_count += 1
        status = f'Thumbnailing: {thumbed_count} / {all_count}'

    total_time = round(time.time() - start_thumb, 2)
    finish_string = f'Last thumbnailing: {total_time} secs {thumbed_count}'
    print(finish_string)
    status = finish_string


def import_scanned():
    global status

    start_import = time.time()

    all_images = ImageFile.select().where(ImageFile.status == FileStatus.SCANNED)
    all_videos = VideoFile.select()
    all_count = len(all_images) + len(all_videos)
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

        status = f'Importing {imported_count} / {all_count}'

    # .where(VideoFile.status == FileStatus.SCANNED)

    for video_db in all_videos:
        imported_count += 1
        try:
            shaHash = sha256sum(video_db.path)
            video_db.hash = shaHash
        except OSError:
            print("Cannot update shaHash for", video_db.path)

        try:
            info = ffmpeg.probe(video_db.path)
            video_db.duration = info['format']['duration']

            video_stream = info['streams'][0]
            video_db.width = video_stream['width']
            video_db.height = video_stream['height']

            if "tags" in video_stream:
                if "creation_time" in video_stream['tags']:
                    creation_time = video_stream['tags']['creation_time']
                    date = datetime.datetime.fromisoformat(creation_time)
                    video_db.taken_date = date

            if "side_data_list" in video_stream and len(video_stream['side_data_list']) > 0:
                side_data = video_stream['side_data_list'][0]
                if "rotation" in side_data:
                    rotation = side_data['rotation']
                    true_width, true_height = calculate_true_resolution(
                        video_stream['width'], video_stream['height'], rotation)
                    video_db.width = true_width
                    video_db.height = true_height

            # print(f"framerate={info['streams'][0]['avg_frame_rate']}")
        except OSError as error:
            print(error)

        video_db.save()
        print('Video imported:', image_db.path, imported_count, '/', all_count)

    total_time = round(time.time() - start_import, 2)
    finish_string = f'Last import: {total_time} secs ({imported_count})'
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


def calculate_true_resolution(width, height, rotation):
    if rotation % 180 == 0:
        return width, height
    else:
        radians = math.radians(rotation)
        new_width = abs(width * math.cos(radians)) + \
            abs(height * math.sin(radians))
        new_height = abs(width * math.sin(radians)) + \
            abs(height * math.cos(radians))
        return int(new_width), int(new_height)
