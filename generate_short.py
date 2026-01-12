import requests
import random
import re
import os
import uuid
import shutil
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

# Google Cloud
from google.cloud import texttospeech

# MoviePy Imports (v2 syntax)
from moviepy import (
    VideoFileClip, AudioFileClip, concatenate_audioclips, 
    concatenate_videoclips, ImageClip, CompositeAudioClip
)
from moviepy.video.VideoClip import TextClip, ColorClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy.audio.AudioClip import AudioArrayClip
from moviepy.video import fx as vfx
from moviepy.audio import fx as afx

# ==========================================
# 1. CONFIGURATION
# ==========================================
@dataclass
class VideoConfig:
    # API & Paths
    google_credentials: str = "google.json"
    temp_audio_dir: str = "temp_audio"
    output_filename: str = "youtube_short.mp4"
    
    # Assets
    subway_video: str = "subway.mp4"
    background_music: str = "music.mp3"
    title_box_image: str = "TitleBox.png"
    body_font: str = "Montserrat-ExtraBold.ttf"
    title_font: str = "Roboto-Regular.ttf"
    
    # Settings
    subreddits: List[str] = field(default_factory=lambda: ["AmIOverreacting", "AmITheAsshole", "rant", "AmITheDevil"])
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    
    # Logic / Testing
    mock_tts: bool = False
    limit_sentences: int = 3  # Set to None for full story
    avg_wpm: int = 180

    def __post_init__(self):
        # Set Env var for Google
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.google_credentials
        # Ensure temp dir exists
        if not os.path.exists(self.temp_audio_dir):
            os.makedirs(self.temp_audio_dir)

# ==========================================
# 2. CONTENT MANAGER (Scraping & Text)
# ==========================================
class ContentManager:
    def __init__(self, config: VideoConfig):
        self.config = config

    def fetch_random_post(self, subreddit: str = None) -> Optional[Dict]:
        """Fetches a post from Reddit."""
        target_sub = subreddit if subreddit else random.choice(self.config.subreddits)
        print(f"🕵️ Fetching from r/{target_sub}...")
        
        try:
            url = f"https://www.reddit.com/r/{target_sub}/hot.json?limit=50"
            headers = {"User-Agent": self.config.user_agent}
            r = requests.get(url, headers=headers)
            if r.status_code != 200: 
                print(f"❌ Error: Status {r.status_code}")
                return None

            data = r.json()
            posts = data['data']['children']
            candidates = []

            for p in posts:
                post_data = p['data']
                if post_data.get('stickied'): continue
                title = post_data.get('title', '')
                body = post_data.get('selftext', '')
                
                if 400 < len(body) < 1200: 
                    candidates.append({"title": title, "body": body})
            
            if candidates:
                return random.choice(candidates)
            return None
        except Exception as e:
            print(f"❌ Error fetching post: {e}")
            return None

    @staticmethod
    def clean_text_for_tts(text: str) -> str:
        """Regex cleanup for cleaner speech."""
        text = re.sub(r'\bAITA\b', "Am I the a-hole", text, flags=re.IGNORECASE)
        text = re.sub(r'\bWIBTA\b', "Would I be the a-hole", text, flags=re.IGNORECASE)
        text = re.sub(r'\((\d+)\s*[Mm]\)', r'\1 male', text)
        text = re.sub(r'\((\d+)\s*[Ff]\)', r'\1 female', text)
        text = re.sub(r'\[(\d+)\s*[Mm]\]', r'\1 male', text)
        text = re.sub(r'\[(\d+)\s*[Ff]\]', r'\1 female', text)
        return text

    @staticmethod
    def split_text_smartly(text: str) -> List[str]:
        """Splits text into sentences."""
        text = text.replace("\n", " ").strip()
        sentences = re.split(r'(?<=[.!?]) +', text)
        return [s for s in sentences if s.strip()]

# ==========================================
# 3. TTS MANAGER (Audio Generation)
# ==========================================
class TTSManager:
    def __init__(self, config: VideoConfig):
        self.config = config
        self.client = None
        
        if not self.config.mock_tts:
            try:
                self.client = texttospeech.TextToSpeechClient()
            except Exception as e:
                print(f"⚠️ Google Creds error: {e}. Switching to Mock TTS.")
                self.config.mock_tts = True

    def generate_audio(self, text: str) -> Tuple[Optional[str], float]:
        """Returns (filepath, duration). If mock, returns (None, duration)."""
        spoken_text = ContentManager.clean_text_for_tts(text)
        word_count = len(spoken_text.split())
        estimated_dur = max(1.0, (word_count / self.config.avg_wpm) * 60)

        if self.config.mock_tts:
            return None, estimated_dur

        try:
            synthesis_input = texttospeech.SynthesisInput(text=spoken_text)
            voice = texttospeech.VoiceSelectionParams(language_code="en-US", name="en-US-Wavenet-C")
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=1.45 
            )
            response = self.client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
            
            filename = os.path.join(self.config.temp_audio_dir, f"tts_{uuid.uuid4()}.mp3")
            with open(filename, "wb") as f:
                f.write(response.audio_content)
            
            # Get exact duration
            with AudioFileClip(filename) as temp_clip:
                actual_dur = temp_clip.duration
            return filename, actual_dur

        except Exception as e:
            print(f"❌ TTS API Error: {e}. Using mock duration.")
            return None, estimated_dur

# ==========================================
# 4. VIDEO ENGINE (Visuals & Composing)
# ==========================================
class VideoEngine:
    def __init__(self, config: VideoConfig):
        self.config = config

    def create_title_card(self, title_text: str, duration: float) -> CompositeVideoClip:
        """Creates the animated title card."""
        def resize_func(t):
            if t < 0.25: return max(0.01, t / 0.25)
            return 1
            
        start_x, start_y = 35, 150
        max_x, max_y = 880, 310 
        max_text_width = max_x - start_x  
        max_text_height = max_y - start_y 
        
        font_size = 60 
        final_txt_clip = None
        
        while font_size > 10:
            temp_txt = TextClip(
                text=title_text, font_size=font_size, color='white', 
                font=self.config.title_font, method='caption',
                size=(max_text_width, None), text_align='left'
            )
            if temp_txt.h <= max_text_height:
                final_txt_clip = temp_txt
                break
            font_size -= 2 
            temp_txt.close()

        if not final_txt_clip:
             final_txt_clip = TextClip(
                text=title_text, font_size=20, color='white', 
                font=self.config.title_font, method='caption', size=(max_text_width, None)
            )

        static_box = ImageClip(self.config.title_box_image).with_duration(duration)
        combined = CompositeVideoClip(
            [static_box, final_txt_clip.with_position((start_x, start_y))], 
            size=(static_box.w, static_box.h)
        ).with_duration(duration)

        anim = combined.with_effects([vfx.Resize(resize_func)])
        
        final_comp = CompositeVideoClip(
            [anim.with_position("center")], size=(1080, 1920)
        ).with_duration(duration).with_effects([vfx.FadeOut(0.5)])
        
        return final_comp

    def create_karaoke_clip(self, sentence: str, audio_duration: float) -> Optional[VideoFileClip]:
        """Creates the word-by-word highlighting clip."""
        words = sentence.split()
        if not words: return None

        total_chars = sum(len(w) for w in words)
        word_durations = [(len(w) / total_chars) * audio_duration for w in words]

        # 1. Measure words
        word_clips_data = [] 
        for w in words:
            temp = TextClip(text=w, font_size=70, font=self.config.body_font, method='label')
            word_clips_data.append({"text": w, "w": temp.w, "h": temp.h})
            temp.close()

        # 2. Layout lines
        lines, current_line, current_line_width = [], [], 0
        max_width = 900
        for data in word_clips_data:
            if current_line_width + data['w'] > max_width:
                lines.append(current_line)
                current_line, current_line_width = [data], data['w']
            else:
                current_line.append(data)
                current_line_width += data['w'] + 20
        if current_line: lines.append(current_line)

        # 3. Create Frames
        pages = [lines[i:i+2] for i in range(0, len(lines), 2)]
        final_clips = []
        word_global_index = 0
        
        for page in pages:
            line_height = max(w['h'] for line in page for w in line) + 15
            total_block_height = len(page) * line_height
            start_y = (1920 - total_block_height) / 2
            
            page_words_flat = [w for line in page for w in line]

            for i, _ in enumerate(page_words_flat):
                current_word_duration = word_durations[word_global_index]
                frame_texts = []
                curr_y = start_y
                temp_idx_counter = 0
                
                for line in page:
                    line_total_width = sum(w['w'] for w in line) + (len(line)-1)*20
                    curr_x = (1080 - line_total_width) / 2
                    
                    for w_data in line:
                        is_active = (temp_idx_counter == i)
                        txt = TextClip(
                            text=w_data['text'],
                            font_size=70,
                            color='yellow' if is_active else 'white',
                            stroke_color='black',
                            stroke_width=6,
                            font=self.config.body_font,
                            method='label'
                        ).with_position((curr_x, curr_y)).with_duration(current_word_duration)
                        
                        frame_texts.append(txt)
                        curr_x += w_data['w'] + 20
                        temp_idx_counter += 1
                    curr_y += line_height

                base = ColorClip(size=(1080, 1920), color=(0,0,0,0), duration=current_word_duration)
                final_clips.append(CompositeVideoClip([base] + frame_texts))
                word_global_index += 1

        return concatenate_videoclips(final_clips)

    def assemble_final_video(self, video_clips, audio_clips):
        """Combines visuals, voiceover, background music, and background video."""
        print("🎬 Assembling Final Video...")
        full_audio = concatenate_audioclips(audio_clips)
        full_text_video = concatenate_videoclips(video_clips)
        total_duration = full_audio.duration

        # Background Music
        if os.path.exists(self.config.background_music):
            bg_music = AudioFileClip(self.config.background_music)
            if bg_music.duration > total_duration:
                max_start = bg_music.duration - total_duration
                start_time = random.uniform(0, max_start)
                bg_music = bg_music.subclipped(start_time, start_time + total_duration)
            else:
                bg_music = concatenate_audioclips([bg_music] * (int(total_duration / bg_music.duration) + 1))
                bg_music = bg_music.subclipped(0, total_duration)
            
            bg_music = bg_music.with_volume_scaled(0.15).with_effects([afx.AudioFadeIn(2.0)])
            full_audio = CompositeAudioClip([full_audio, bg_music])
        else:
            print(f"⚠️ Warning: Music {self.config.background_music} not found.")

        # Background Video
        bg = VideoFileClip(self.config.subway_video).without_audio()
        if bg.duration < total_duration:
            bg = bg.with_effects([vfx.Loop(duration=total_duration + 1)])
        
        # Crop Logic
        target_ratio = 1080 / 1920
        bg_ratio = bg.w / bg.h
        if bg_ratio > target_ratio:
            bg = bg.resized(height=1920)
        else:
            bg = bg.resized(width=1080)
        bg = bg.cropped(width=1080, height=1920, x_center=bg.w/2, y_center=bg.h/2)
        
        # Final Composite
        final = CompositeVideoClip([bg, full_text_video], size=(1080, 1920))
        final = final.with_audio(full_audio).with_duration(total_duration)
        
        return final, bg # return bg handle to close later

# ==========================================
# 5. PIPELINE (The Controller)
# ==========================================
class VideoPipeline:
    def __init__(self, config: VideoConfig):
        self.config = config
        self.content_mgr = ContentManager(config)
        self.tts_mgr = TTSManager(config)
        self.video_engine = VideoEngine(config)
        self.audio_resources = []

    def run(self, specific_post=None):
        """Runs the full pipeline. pass `specific_post` dict to skip scraping."""
        
        # 1. Get Content
        post = specific_post if specific_post else self.content_mgr.fetch_random_post()
        if not post: return

        print(f"🚀 Processing: {post['title']}")
        
        # 2. Process Text
        title_text = post["title"]
        body_sentences = self.content_mgr.split_text_smartly(post["body"])
        
        if self.config.limit_sentences:
            print(f"✂️ Limiting to {self.config.limit_sentences} sentences.")
            body_sentences = body_sentences[:self.config.limit_sentences]

        # 3. Generate Assets
        all_video_segments = []
        all_audio_clips = []

        try:
            # Title
            print("   Generating Title...")
            t_file, t_dur = self.tts_mgr.generate_audio(title_text)
            self._add_audio_clip(t_file, t_dur, all_audio_clips)
            all_video_segments.append(self.video_engine.create_title_card(title_text, t_dur))

            # Body
            print(f"   Generating {len(body_sentences)} body sentences...")
            for sentence in body_sentences:
                if not sentence.strip(): continue
                a_file, a_dur = self.tts_mgr.generate_audio(sentence)
                self._add_audio_clip(a_file, a_dur, all_audio_clips)
                
                vid_seg = self.video_engine.create_karaoke_clip(sentence, a_dur)
                if vid_seg: all_video_segments.append(vid_seg)

            # 4. Assembly & Render
            final_video, bg_handle = self.video_engine.assemble_final_video(all_video_segments, all_audio_clips)
            
            final_video.write_videofile(
                self.config.output_filename, 
                fps=24 if self.config.mock_tts else 30, 
                codec="libx264", 
                audio_codec="aac",
                threads=4
            )
            
            # Cleanup Handles
            bg_handle.close()
            final_video.close()

        finally:
            self._cleanup()

    def _add_audio_clip(self, file_path, duration, list_ref):
        """Helper to handle mock silence vs real audio files."""
        if file_path is None:
            # Mock Silence
            silence = AudioArrayClip(np.zeros((int(44100 * duration), 2)), fps=44100)
            list_ref.append(silence)
        else:
            clip = AudioFileClip(file_path)
            list_ref.append(clip)
            self.audio_resources.append(clip) # Keep track to close later

    def _cleanup(self):
        """Clean up open files and temp directory."""
        print("🧹 Cleaning up...")
        for c in self.audio_resources: 
            try: c.close()
            except: pass
            
        if os.path.exists(self.config.temp_audio_dir):
            shutil.rmtree(self.config.temp_audio_dir)


# ==========================================
# 6. USAGE EXAMPLES
# ==========================================
if __name__ == "__main__":
    
    # EXAMPLE 1: Standard Run (Scrape Reddit + Create Video)
    # config = VideoConfig()
    # pipeline = VideoPipeline(config)
    # pipeline.run()

    # EXAMPLE 2: Test Specific Part (Mock TTS, Fast Render)
    print("--- 🧪 STARTING TEST RUN ---")
    test_config = VideoConfig(
        mock_tts=True,       # Don't use Google API
        limit_sentences=2,   # Only do title + 2 sentences
        output_filename="test_render.mp4"
    )
    
    # Inject a fake story to test logic without scraping
    fake_post = {
        "title": "Test Title for Development",
        "body": "This is the first sentence to test the karaoke. And here is a second shorter one."
    }
    
    pipeline = VideoPipeline(test_config)
    pipeline.run(specific_post=fake_post)
    print("--- ✅ TEST COMPLETE ---")