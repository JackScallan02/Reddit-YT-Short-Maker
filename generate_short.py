import requests
import random
import re
import os
import uuid
import numpy as np
from google.cloud import texttospeech

# MoviePy Imports
from moviepy import VideoFileClip, AudioFileClip, concatenate_audioclips, concatenate_videoclips
from moviepy.video.VideoClip import TextClip, ColorClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
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
GOOGLE_CREDENTIALS_PATH = "google.json"
FONT_PATH = "Montserrat-ExtraBold.ttf" # Ensure this file exists

# Google TTS Setup
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_CREDENTIALS_PATH
tts_client = texttospeech.TextToSpeechClient()

# -----------------------------
# 1. REDDIT SCRAPING
# -----------------------------
def fetch_random_post(subreddit):
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
        
        # 500-800 chars fits well in ~50-60s
        if 500 < len(body) < 800:
            candidates.append({"title": title, "body": body})
    
    if candidates:
        selected = random.choice(candidates)
        full_text = f"{selected['title']}. {selected['body']}"
        return {"title": selected['title'], "body": full_text}
    return None

# -----------------------------
# 2. SENTENCE SPLITTING
# -----------------------------
def split_text_smartly(text):
    text = text.replace("\n", " ").strip()
    # Split by '.', '!', '?' followed by a space
    sentences = re.split(r'(?<=[.!?]) +', text)
    return [s for s in sentences if s.strip()]

# -----------------------------
# 3. TTS GENERATION
# -----------------------------
def generate_audio_for_sentence(sentence):
    synthesis_input = texttospeech.SynthesisInput(text=sentence)
    voice = texttospeech.VoiceSelectionParams(language_code="en-US", name="en-US-Wavenet-C")
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.3 
    )
    response = tts_client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    filename = f"tts_{uuid.uuid4()}.mp3"
    with open(filename, "wb") as f:
        f.write(response.audio_content)
    return filename

# -----------------------------
# 4. THE KARAOKE LAYOUT ENGINE
# -----------------------------
def create_karaoke_clip(sentence, duration, font_size=70, max_width=900):
    """
    Creates a video clip for a sentence where words highlight one by one.
    Handles word wrapping manually to ensure layout stability.
    """
    words = sentence.split()
    if not words:
        return None

    # Time per word (linear distribution)
    time_per_word = duration / len(words)

    # --- Step A: Calculate Positions (Layout) ---
    # We must pre-calculate where every word goes so they don't jump around
    word_clips_data = [] # Stores (text, size_w, size_h)
    
    # Create temp clips just to measure size
    for w in words:
        # We use a dummy clip to get the width/height of the font
        temp = TextClip(text=w, font_size=font_size, font=FONT_PATH, method='label')
        word_clips_data.append({"text": w, "w": temp.w, "h": temp.h})
        temp.close()

    # Calculate X,Y coordinates for wrapping
    lines = []
    current_line = []
    current_line_width = 0
    
    # Group words into lines based on max_width
    for data in word_clips_data:
        word_w = data['w']
        # Add some spacing between words (e.g., 20px)
        spacing = 20 
        
        if current_line_width + word_w > max_width:
            # Push current line and start new one
            lines.append(current_line)
            current_line = [data]
            current_line_width = word_w
        else:
            current_line.append(data)
            current_line_width += word_w + spacing
    
    if current_line:
        lines.append(current_line)

    # --- Step B: Create The Frames ---
    # We need to generate a "sub-clip" for every word step
    
    clips_sequence = []
    
    # Helper to calculate line height (take max height of any word)
    line_height = max(d['h'] for d in word_clips_data) + 10
    total_text_block_height = len(lines) * line_height
    
    # Calculate start Y to center the block vertically on screen (1920 height)
    # or just center it relative to the clip we return
    start_y = (1920 - total_text_block_height) / 2
    
    global_word_index = 0
    
    # Loop through every word in the sentence sequence
    for i in range(len(words)):
        
        # Build the visual for this specific step (where word 'i' is yellow)
        frame_clips = []
        
        current_y = start_y
        current_global_idx = 0
        
        for line in lines:
            # Calculate total width of this line to center it horizontally
            line_total_width = sum(w['w'] for w in line) + (len(line)-1)*20
            start_x = (1080 - line_total_width) / 2
            
            curr_x = start_x
            
            for word_data in line:
                # DECIDE COLOR: Is this the active word?
                is_active = (current_global_idx == i)
                color = 'yellow' if is_active else 'white'
                stroke_width = 4 if is_active else 2
                
                # Create the actual text clip
                txt = (TextClip(
                        text=word_data['text'],
                        font_size=font_size,
                        color=color,
                        stroke_color='black',
                        stroke_width=stroke_width,
                        font=FONT_PATH,
                        method='label' 
                       )
                       .with_position((curr_x, current_y))
                       .with_duration(time_per_word)
                      )
                
                frame_clips.append(txt)
                
                # Move X cursor
                curr_x += word_data['w'] + 20
                current_global_idx += 1
            
            # Move Y cursor for next line
            current_y += line_height

        # Create the Composite for this 0.X second chunk
        # We use a transparent ColorClip as base to ensure size is correct
        base = ColorClip(size=(1080, 1920), color=(0,0,0,0), duration=time_per_word)
        step_composite = CompositeVideoClip([base] + frame_clips)
        clips_sequence.append(step_composite)

    # Concatenate all the small steps into one big clip for the sentence
    return concatenate_videoclips(clips_sequence)

# -----------------------------
# 5. MAIN VIDEO LOGIC
# -----------------------------
def make_video(post_data):
    full_text = post_data["body"]
    sentences = split_text_smartly(full_text)
    
    all_audio_clips = []
    all_video_segments = []
    
    print(f"--- Processing {len(sentences)} sentences... ---")

    for sentence in sentences:
        # 1. Audio
        audio_file = generate_audio_for_sentence(sentence)
        audio_clip = AudioFileClip(audio_file)
        all_audio_clips.append(audio_clip)
        
        # 2. Video (Karaoke)
        # We generate the complex text animation for this sentence
        video_segment = create_karaoke_clip(sentence, audio_clip.duration)
        if video_segment:
            all_video_segments.append(video_segment)

    # Combine everything
    full_audio = concatenate_audioclips(all_audio_clips)
    full_text_video = concatenate_videoclips(all_video_segments)
    
    total_duration = full_audio.duration
    print(f"--- Total Duration: {total_duration:.2f}s ---")

    # Background Setup
    bg = VideoFileClip(SUBWAY_VIDEO).without_audio()
    
    # Random Start
    if bg.duration > total_duration + 5:
        max_start = bg.duration - (total_duration + 2)
        start_time = random.uniform(0, max_start)
        bg = bg.subclipped(start_time, start_time + total_duration + 1)
    else:
        bg = bg.with_effects([vfx.Loop(duration=total_duration + 1)])
        
    bg = bg.resized(height=1920)
    bg = bg.with_position("center") # Center the huge video
    
    # Final Composite
    # Layer: [Background] -> [Karaoke Text]
    final = CompositeVideoClip(
        [bg, full_text_video], 
        size=(1080, 1920)
    )
    
    final.duration = total_duration
    final = final.with_audio(full_audio)
    
    if final.duration > 59.0:
        final = final.subclipped(0, 59.0)

    final.write_videofile(
        OUTPUT_VIDEO, 
        fps=30, 
        codec="libx264", 
        audio_codec="aac",
        ffmpeg_params=["-pix_fmt", "yuv420p"]
    )

    # Cleanup
    for c in all_audio_clips: c.close()
    bg.close()

# -----------------------------
# MAIN
# -----------------------------
if __name__ == "__main__":
    post = fetch_random_post("AmITheAsshole")
    if post:
        print(f"\nTITLE: {post['title']}")
        make_video(post)
        print(f"\n🎉 DONE! Saved as: {OUTPUT_VIDEO}")
    else:
        print("\n❌ Could not find a suitable post.")