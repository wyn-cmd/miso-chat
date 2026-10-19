import os
import requests
import sys

def scrape_to_markdown(url, output_dir, filename):
    # Using Jina Reader (r.jina.ai), which is specifically designed to
    # turn any URL into LLM-friendly Markdown and bypass bot protections.
    reader_url = f"https://r.jina.ai/{url}"
    
    try:
        print(f"Fetching from Jina Reader: {reader_url}")
        response = requests.get(reader_url)
        response.raise_for_status()
        
        # Filter content to remove everything from 'Opt-Out Request Honored' onwards
        text = response.text
        cutoff = "Opt-Out Request Honored"
        if cutoff in text:
            text = text.split(cutoff)[0]
        
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, filename)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"Successfully saved clean Markdown to {path}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python scrape_wiki.py <url> <output_dir> <filename>")
    else:
        scrape_to_markdown(sys.argv[1], sys.argv[2], sys.argv[3])
