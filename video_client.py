import requests

def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    """
    Uploads a video file to the server and returns its URL.
    """
    url = f"{server_url.rstrip('/')}/upload"
    
    # Извлекаем имя файла из пути (работает и для Windows, и для Unix)
    filename = video_path.replace('\\', '/').split('/')[-1]
    
    with open(video_path, 'rb') as f:
        files = {'file': (filename, f)}
        data = {'company_name': company_name}
        response = requests.post(url, files=files, data=data)
    
    response.raise_for_status()
    return response.json()["video_url"]


def GetVideoURL(server_url: str, company_name: str) -> str:
    """
    Returns the video URL for the given company name.
    """
    url = f"{server_url.rstrip('/')}/get_video_url"
    response = requests.get(url, params={"company_name": company_name})
    
    response.raise_for_status()
    return response.json()["video_url"]
