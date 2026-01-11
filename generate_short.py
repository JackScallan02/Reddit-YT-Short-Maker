import requests
import random
import re
import os
import uuid
import shutil
import numpy as np
from google.cloud import texttospeech

# MoviePy Imports (v2 syntax)
from moviepy import VideoFileClip, AudioFileClip, concatenate_audioclips, concatenate_videoclips, ImageClip
from moviepy.video.VideoClip import TextClip, ColorClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy.audio.AudioClip import AudioArrayClip
from moviepy.video import fx as vfx

# -----------------------------
# CONFIG
# -----------------------------
SUBREDDITS = ["AmIOverreacting", "AmITheAsshole", "rant", "AmITheDevil"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/115.0.0.0 Safari/537.36",
}
SUBWAY_VIDEO = "subway.mp4" 
OUTPUT_VIDEO = "youtube_short.mp4"
TITLE_BOX_IMAGE = "TitleBox.png"
GOOGLE_CREDENTIALS_PATH = "google.json"
TEMP_AUDIO_DIR = "temp_audio"

# FONTS
BODY_FONT_PATH = "Montserrat-ExtraBold.ttf"  
TITLE_FONT_PATH = "Roboto-Regular.ttf"       

# --- TESTING CONTROLS ---
MOCK_TTS = False          
LIMIT_SENTENCES = 3      
AVG_WPM = 150            

# Google TTS Setup
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_CREDENTIALS_PATH
try:
    tts_client = texttospeech.TextToSpeechClient()
except Exception:
    if not MOCK_TTS:
        print("Warning: Google Credentials not found. Switching to MOCK_TTS=True")
        MOCK_TTS = True

if not os.path.exists(TEMP_AUDIO_DIR):
    os.makedirs(TEMP_AUDIO_DIR)

# -----------------------------
# 1. REDDIT SCRAPING
# -----------------------------
def fetch_random_post(subreddit):
    try:
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=50"
        r = requests.get(url, headers=HEADERS)
        if r.status_code != 200: return None

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
            selected = random.choice(candidates)
            return {"title": selected['title'], "body": selected['body']}
        return None
    except Exception as e:
        print(f"Error fetching post: {e}")
        return None

# -----------------------------
# 2. TEXT PROCESSING & TTS CLEANING
# -----------------------------
def clean_text_for_tts(text):
    """Replaces Reddit shorthand with spoken-word equivalents."""
    # AITA -> Am I the a-hole
    text = re.sub(r'\bAITA\b', "Am I the a-hole", text, flags=re.IGNORECASE)
    # WIBTA -> Would I be the a-hole
    text = re.sub(r'\bWIBTA\b', "Would I be the a-hole", text, flags=re.IGNORECASE)
    
    # Ages/Genders: (24M) -> 24 male, (19F) -> 19 female
    text = re.sub(r'\((\d+)\s*[Mm]\)', r'\1 male', text)
    text = re.sub(r'\((\d+)\s*[Ff]\)', r'\1 female', text)
    text = re.sub(r'\[(\d+)\s*[Mm]\]', r'\1 male', text)
    text = re.sub(r'\[(\d+)\s*[Ff]\]', r'\1 female', text)
    
    return text

def split_text_smartly(text):
    text = text.replace("\n", " ").strip()
    sentences = re.split(r'(?<=[.!?]) +', text)
    return [s for s in sentences if s.strip()]

# -----------------------------
# 3. TTS GENERATION
# -----------------------------
def generate_audio_for_sentence(sentence):
    # Clean text specifically for the spoken audio
    spoken_text = clean_text_for_tts(sentence)
    
    word_count = len(spoken_text.split())
    estimated_duration = max(1.5, (word_count / AVG_WPM) * 60)
    
    if MOCK_TTS:
        return None, estimated_duration

    try:
        synthesis_input = texttospeech.SynthesisInput(text=spoken_text)
        voice = texttospeech.VoiceSelectionParams(language_code="en-US", name="en-US-Wavenet-C")
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.2 
        )
        response = tts_client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
        
        filename = os.path.join(TEMP_AUDIO_DIR, f"tts_{uuid.uuid4()}.mp3")
        with open(filename, "wb") as f:
            f.write(response.audio_content)
        
        with AudioFileClip(filename) as temp_clip:
            actual_dur = temp_clip.duration
        return filename, actual_dur
    except Exception as e:
        print(f"TTS Error: {e}. Falling back to mock.")
        return None, estimated_duration

# -----------------------------
# 4. TITLE CARD GENERATOR
# -----------------------------
def create_title_card(title_text, duration):
    box_clip = ImageClip(TITLE_BOX_IMAGE).with_duration(duration)
    
    # Coordinates (923x381)
    # Adding a 10px internal safety margin to max_x to prevent side-clipping
    start_x, start_y = 35, 150
    max_x, max_y = 880, 310 
    
    max_text_width = max_x - start_x  
    max_text_height = max_y - start_y 
    
    font_size = 60 
    final_txt_clip = None
    
    while font_size > 10:
        # We use 'caption' mode here. It's much safer for fitting text in boxes.
        temp_txt = TextClip(
            text=title_text, 
            font_size=font_size, 
            color='white', 
            font=TITLE_FONT_PATH,
            method='caption',
            size=(max_text_width, None), 
            text_align='left'
        )
        
        if temp_txt.h <= max_text_height:
            final_txt_clip = temp_txt
            break
            
        font_size -= 2 
        temp_txt.close()

    if not final_txt_clip:
         final_txt_clip = TextClip(
            text=title_text, font_size=20, color='white', 
            font=TITLE_FONT_PATH, method='caption', size=(max_text_width, None)
        )

    # Composite the text at start_x, start_y relative to the box
    title_graphic = CompositeVideoClip(
        [box_clip, final_txt_clip.with_position((start_x, start_y))], 
        size=(box_clip.w, box_clip.h)
    ).with_duration(duration)
    
    final_comp = CompositeVideoClip(
        [title_graphic.with_position("center")], 
        size=(1080, 1920)
    ).with_duration(duration)
    
    return final_comp

# -----------------------------
# 5. KARAOKE LAYOUT ENGINE
# -----------------------------
def create_karaoke_clip(sentence, audio_duration, font_size=70, max_width=900):
    words = sentence.split()
    if not words: return None

    total_chars = sum(len(w) for w in words)
    word_durations = [(len(w) / total_chars) * audio_duration for w in words]

    word_clips_data = [] 
    for w in words:
        temp = TextClip(text=w, font_size=font_size, font=BODY_FONT_PATH, method='label')
        word_clips_data.append({"text": w, "w": temp.w, "h": temp.h})
        temp.close()

    lines, current_line, current_line_width = [], [], 0
    for data in word_clips_data:
        if current_line_width + data['w'] > max_width:
            lines.append(current_line)
            current_line, current_line_width = [data], data['w']
        else:
            current_line.append(data)
            current_line_width += data['w'] + 20
    if current_line: lines.append(current_line)

    pages = [lines[i:i+2] for i in range(0, len(lines), 2)]
    final_clips = []
    word_global_index = 0
    
    for page in pages:
        line_height = max(w['h'] for line in page for w in line) + 15
        total_block_height = len(page) * line_height
        start_y = (1920 - total_block_height) / 2
        
        page_words_flat = [w for line in page for w in line]

        for i, target_word_data in enumerate(page_words_flat):
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
                        font_size=font_size,
                        color='yellow' if is_active else 'white',
                        stroke_color='black',
                        stroke_width=6,
                        font=BODY_FONT_PATH,
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

# -----------------------------
# 6. MAIN LOGIC
# -----------------------------
def make_video(post_data):
    title_text = post_data["title"]
    body_sentences = split_text_smartly(post_data["body"])
    
    if MOCK_TTS and LIMIT_SENTENCES:
        print(f"🧪 MOCK MODE: processing title + {LIMIT_SENTENCES} sentences.")
        body_sentences = body_sentences[:LIMIT_SENTENCES]
    
    all_audio_clips = []
    all_video_segments = []

    try:
        print("Creating Title Card...")
        title_audio_file, title_dur = generate_audio_for_sentence(title_text)
        
        if MOCK_TTS or title_audio_file is None:
            silence = AudioArrayClip(np.zeros((int(44100 * title_dur), 2)), fps=44100)
            all_audio_clips.append(silence)
        else:
            all_audio_clips.append(AudioFileClip(title_audio_file))
            
        all_video_segments.append(create_title_card(title_text, title_dur))

        for sentence in body_sentences:
            if not sentence.strip(): continue
            
            audio_file, duration = generate_audio_for_sentence(sentence)
            
            if MOCK_TTS or audio_file is None:
                silence = AudioArrayClip(np.zeros((int(44100 * duration), 2)), fps=44100)
                all_audio_clips.append(silence)
            else:
                all_audio_clips.append(AudioFileClip(audio_file))
            
            video_segment = create_karaoke_clip(sentence, duration)
            if video_segment: all_video_segments.append(video_segment)

        full_audio = concatenate_audioclips(all_audio_clips)
        full_text_video = concatenate_videoclips(all_video_segments)
        total_duration = full_audio.duration

        # --- BACKGROUND LOGIC (CROP-TO-FILL) ---
        bg = VideoFileClip(SUBWAY_VIDEO).without_audio()
        if bg.duration < total_duration:
            bg = bg.with_effects([vfx.Loop(duration=total_duration + 1)])
        
        target_ratio = 1080 / 1920
        bg_ratio = bg.w / bg.h
        if bg_ratio > target_ratio:
            bg = bg.resized(height=1920)
        else:
            bg = bg.resized(width=1080)
            
        bg = bg.cropped(width=1080, height=1920, x_center=bg.w/2, y_center=bg.h/2)
        
        # --- FINAL ASSEMBLY ---
        final = CompositeVideoClip([bg, full_text_video], size=(1080, 1920))
        final = final.with_audio(full_audio).with_duration(total_duration)

        final.write_videofile(
            OUTPUT_VIDEO, 
            fps=24 if MOCK_TTS else 30, 
            codec="libx264", 
            audio_codec="aac",
            threads=4
        )
        
        bg.close()
        final.close()

    finally:
        for c in all_audio_clips: c.close()
        if os.path.exists(TEMP_AUDIO_DIR):
            shutil.rmtree(TEMP_AUDIO_DIR)

if __name__ == "__main__":
    post = fetch_random_post("AmITheAsshole")
    if post:
        print(f"\nSTARTING: {post['title']}")
        make_video(post)
        print(f"\n🎉 DONE: {OUTPUT_VIDEO}")