import os
from PIL import Image

def resize_icons():
    source_path = 'logo_header.png'
    sizes = [16, 48, 128]
    
    if not os.path.exists(source_path):
        print(f"Error: Source image {source_path} not found.")
        return

    try:
        with Image.open(source_path) as img:
            for size in sizes:
                new_img = img.resize((size, size), Image.Resampling.LANCZOS)
                output_filename = f'icon{size}.png'
                new_img.save(output_filename)
                print(f"Generated {output_filename}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    resize_icons()
