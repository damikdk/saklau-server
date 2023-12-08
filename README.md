### How to run

`python -B -m uvicorn saklau-server:app --host 0.0.0.0 --port 8000 --reload`

Check [http://localhost:8000](http://localhost:8000)

### What's next

1. On start, it will create database in root.
2. On request to `/scan`, it will scan everything in `/media` folder (recursively).
3. On request to `/import`, it will parse some meta of scanned files. Take a while: (1700 files in 5 minutes).
   - `width` and `height` (Images EXIF, Videos `ffmpeg.probe`)
   - `phash` (Images `imagehash`)
   - `taken_date` (Images EXIF, Videos `ffmpeg.probe`)
   - `geo` (`GPSLatitude` `GPSLongitude`) (Images EXIF)
   - `duration` (Videos `ffmpeg.probe`)
4. On request to `/thumb`, it will generate thumbnails in `/cache` folder. Take a while (1700 files in 7 minutes).
   - Aspect ratio safe, fit in, maximum resolution is `256x256`
   - `.jpg` for images by `PIL.Image.thumbnail()` (~2-10kb)
   - `.webp`for videos by `ffmpeg` from first 2 seconds of video, 10 fps (~50-200kb)
5. On request to `/`, it returns array of all scanned media files
