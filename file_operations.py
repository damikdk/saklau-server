from os import walk, path, makedirs
import time
import datetime
import math
from fastapi import BackgroundTasks
import ffmpeg
from PIL import Image
from pillow_heif import register_heif_opener
from PIL.ExifTags import TAGS, GPSTAGS
import imagehash
import hashlib
import mimetypes
from models import File, FileStatus, ImageFile, TextFile, VideoFile

mimetypes.init()
register_heif_opener()

THUMBNAIL_SIZE = (256, 256)
THUMB_FOLDER_NAME = 'cache'
THUMB_FORMAT = 'jpg'
HERE_PATH = '.'


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
    finish_string = 'Scan of ' + \
        str(len(new_items)) + ' is over in ' + str(total_time)
    print(finish_string)
    status = finish_string

    return new_items


def thumb():
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

    all_videos = VideoFile.select()
    # .where(VideoFile.status == FileStatus.SCANNED)

    for video_db in all_videos:
        thumbnail_path = path.join(
            THUMB_FOLDER_NAME, str(video_db.id)) + '.webp'

        if path.exists(thumbnail_path):
            # print('Already cached:', file_path)
            continue

        ffmpeg.input(video_db.path).output(thumbnail_path,
                                           vcodec='libwebp',
                                           vf="fps=fps=10,scale='min(256,iw)':min'(256,ih)':force_original_aspect_ratio=decrease",
                                           lossless=1,
                                           loop=0,
                                           fps_mode="passthrough",
                                           preset='default',
                                           format='webp',
                                           t=2).run()


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

        status = f'Importing {imported_count} / {all_count}'

    total_time = round(time.time() - start_import, 2)

    finish_string = f'Last import: {total_time} secs ({imported_count})'
    print(finish_string)
    status = finish_string

    all_videos = VideoFile.select()
    # .where(VideoFile.status == FileStatus.SCANNED)

    for video_db in all_videos:
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


def import_files(background_tasks: BackgroundTasks):
    pass


def read_main():
    pass


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
