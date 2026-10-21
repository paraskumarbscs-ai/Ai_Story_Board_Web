import asyncio
import json
import os
import time
import urllib.parse
import edge_tts
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from google import genai
from pydantic import BaseModel
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# YAHAN APNI GEMINI API KEY DALEN
GEMINI_API_KEY = "AQ.Ab8RN6KxcwHSLTxtSRr9AVwj6zXIHSM03yIaDMyUUbGnNBG7OA"

os.makedirs("output_videos", exist_ok=True)
app.mount(
    "/media", StaticFiles(directory="output_videos"), name="media"
)

@app.get("/", response_class=HTMLResponse)
async def serve_home():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Error: index.html not found!</h1>"

class FullVideoRequest(BaseModel):
    script_text: str
    art_style: str
    language: str

@app.post("/api/generate-full-short")
async def generate_full_short(data: FullVideoRequest):
    if len(data.script_text.strip()) < 10:
        raise HTTPException(
            status_code=400, detail="Script too short"
        )

    # 1. AI STORYBOARD GENERATION (FULL STORY PRESERVATION & GENDER FIX)
    lang_rule = ""
    if "Urdu" in data.language:
        lang_rule = "Write the 'narration' strictly in pure Urdu script (e.g. ایک بادشاہ تھا)."
    elif "Hindi" in data.language:
        lang_rule = "Write the 'narration' strictly in pure Hindi Devanagari script (e.g. एक राजा था)."

    prompt = f"""
    Break this story into logical visual scenes based on the flow of the text. 
    {lang_rule}
    
    CRITICAL DIRECTOR INSTRUCTIONS: 
    1. DO NOT SUMMARIZE OR CUT THE TEXT. You must include the COMPLETE original story in the narrations. Do not skip any character names, details, or dialogue.
    2. UNIVERSAL PRONUNCIATION RULE: If the story contains English words or technical terms, either translate them into natural native words OR spell them phonetically in the native script so the TTS engine pronounces them correctly.
    3. STRICT CHARACTER DESCRIPTIONS (CRITICAL): Image AI does not understand names. In the 'visual_prompt', you MUST explicitly describe the gender, age, and relationship of the characters. 
       - NEVER just write "Ali and Aisha". INSTEAD, write: "1 young man and 1 young woman, a romantic couple."
       - ALWAYS specify "male" or "female" clearly so the image generator doesn't make mistakes.

    Return ONLY valid JSON Array. Keys must be exactly: 
    - scene_number (int)
    - narration (string: strictly native script for AI voice, containing the full text for that scene)
    - visual_prompt (string: A highly descriptive scene setup specifying EXACT genders)
    """
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt + "\nStory:\n" + data.script_text,
        )
        clean_json = (
            res.text.replace("```json", "")
            .replace("```", "")
            .strip()
        )
        scenes = json.loads(clean_json)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"AI Brain Error: {str(e)}"
        )

    # 2. RENDER ENGINE (IMAGE + WATERMARK + AUDIO = VIDEO)
    generated_clips = []
    
    voice = "en-US-ChristopherNeural"
    if "Urdu" in data.language:
        voice = "ur-PK-AsadNeural"
    elif "Hindi" in data.language:
        voice = "hi-IN-MadhurNeural"

    try:
        for s in scenes:
            s_num = s["scene_number"]
            img_path = f"output_videos/temp_img_{s_num}.jpg"
            aud_path = f"output_videos/temp_aud_{s_num}.mp3"

            # A. Download Background Image (FORCE ART STYLE)
            strict_image_prompt = f"{s['visual_prompt']}, {data.art_style}, 8k resolution, highly detailed masterpiece"
            
            enc_prompt = urllib.parse.quote(strict_image_prompt)
            img_url = f"https://image.pollinations.ai/prompt/{enc_prompt}?width=1080&height=1920&nologo=true"
            r = requests.get(img_url, headers={"User-Agent": "Mozilla/5.0"})
            with open(img_path, "wb") as f:
                f.write(r.content)

            # B. DIRECT WATERMARK (SMART FIX - No ImageMagick Needed)
            try:
                img = Image.open(img_path)
                draw = ImageDraw.Draw(img)
                img_w, img_h = img.size
                
                # Font ki setting
                try:
                    font = ImageFont.truetype("arialbd.ttf", 45) # Bold font
                except:
                    font = ImageFont.load_default()
                    
                channel_name = "@par*pay*_ai_creator"
                
                # Text ko screen ke neeche center me lagana (White text, Black border)
                draw.text(
                    (img_w / 2, img_h - 150), 
                    channel_name, 
                    font=font, 
                    fill="white", 
                    stroke_width=3, 
                    stroke_fill="black", 
                    anchor="ma"
                )
                img.save(img_path)
            except Exception as e:
                print("Watermark Error:", e)

            # C. Generate Voice
            comm = edge_tts.Communicate(s["narration"], voice)
            await comm.save(aud_path)

            # D. Compile Clip with ZOOM Effect
            aclip = AudioFileClip(aud_path)
            duration = aclip.duration
            
            # Zoom logic: Har frame ko waqt ke sath 10% bada karna
            def zoom_effect(t):
                return 1.0 + 0.1 * (t / duration)

            iclip = (
                ImageClip(img_path)
                .with_duration(duration)
                .resized(zoom_effect)          # Zoom apply kiya
                .with_position("center")       # Screen ke center me rakha
                .with_audio(aclip)
            )
            generated_clips.append(iclip)

        # 3. STITCH ALL SCENES
        final_video = concatenate_videoclips(generated_clips, method="compose")
        
        file_name = f"Viral_Short_{int(time.time())}.mp4"
        save_path = f"output_videos/{file_name}"

        # Final render
        final_video.write_videofile(
            save_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None,
        )

        final_video.close()
        for c in generated_clips:
            c.close()

        return {
            "status": "success",
            "video_url": f"http://127.0.0.1:8000/media/{file_name}",
            "script_breakdown": scenes,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Rendering Error: {str(e)}"
        )