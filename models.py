from peewee import SqliteDatabase, Model, CharField, UUIDField, FloatField, DateTimeField, IntegerField
import datetime
import uuid
from enum import StrEnum

db = SqliteDatabase("data.db")


class FileStatus(StrEnum):
    SCANNED = 'SCANNED'
    IMPORTED = 'IMPORTED'
    TRASHED = 'TRASHED'


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


db.create_tables([File, ImageFile, VideoFile, TextFile], safe=True)
