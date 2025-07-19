import os
import boto3


class AudioSource:

    def list_sounds(self):
        raise NotImplementedError

    async def download(self, sound_name, dest_dir):
        raise NotImplementedError


class LocalAudioSource(AudioSource):

    def __init__(self, audio_dir="sounds"):
        self.audio_dir = audio_dir

    def list_sounds(self):
        files = [f for f in os.listdir(self.audio_dir) if f.endswith('.ogg')]
        return [os.path.splitext(f)[0] for f in files]

    async def download(self, sound_name, dest_dir):
        fname = f"{sound_name}.ogg"
        src_path = os.path.join(self.audio_dir, fname)
        dest_path = os.path.join(dest_dir, fname)
        if not os.path.exists(src_path):
            raise FileNotFoundError(f"Local file not found: {src_path}")
        with open(src_path, "rb") as src, open(dest_path, "wb") as dst:
            dst.write(src.read())
        return dest_path


class S3AudioSource(AudioSource):

    def __init__(self, bucket_name):
        self.s3 = boto3.client('s3')
        self.bucket = bucket_name

    def list_sounds(self):
        response = self.s3.list_objects_v2(Bucket=self.bucket)
        return [
            os.path.splitext(os.path.basename(obj['Key']))[0]
            for obj in response.get('Contents', [])
            if obj['Key'].endswith('.ogg')
        ]

    async def download(self, sound_name, dest_dir):
        fname = f"{sound_name}.ogg"
        dest_path = os.path.join(dest_dir, fname)
        self.s3.download_file(self.bucket, fname, dest_path)
        return dest_path
