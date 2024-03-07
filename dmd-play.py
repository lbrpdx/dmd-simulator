#!/usr/bin/env python3

from PIL import Image, ImageDraw, ImageFont
import sys
import numpy as np
import argparse
import time
import socket
import re

feature_video = False
try:
    import cv2
    feature_video = True
except:
    pass

class DmdPlayer:

    def im2rgb565(im):
        data = np.array(im.convert('RGB'))
        R5 = (data[...,0]>>3).astype(np.uint16) << 11
        G6 = (data[...,1]>>2).astype(np.uint16) << 5
        B5 = (data[...,2]>>3).astype(np.uint16)
        return R5 | G6 | B5

    def imageConvert(im):
        return DmdPlayer.im2rgb565(im)
        #return np.array(im.convert('RGB'))

    def sendFrame(client, layer, im):
        msg = bytearray(str(layer + "\n" + str(len(im.tobytes()))) + "\n", "utf-8") + im.tobytes()
        msglen = len(msg)
        totalsent = 0
        while totalsent < msglen:
            sent = client.send(msg[totalsent:])
            if sent == 0:
                raise RuntimeError("socket connection broken")
            totalsent = totalsent + sent
        
    def imageFit(im, width, height, padding = True):
        img_width, img_height = im.size
        woffset = 0
        hoffset = 0
        if img_height == 0 or img_width == 0:
            return Image.new("RGBA", (width, height))

        if(img_width / img_height > width / height):
            new_width = width
            if padding:
                new_height = round(width * img_height / img_width)
            else:
                new_height = height
            if padding:
                hoffset = (height - new_height) // 2
        else:
            if padding:
                new_width = round(height * img_width / img_height)
            else:
                new_width = width
            new_height = height
            if padding:
                woffset = (width - new_width) // 2
        im = im.resize((new_width, new_height))
        # need rgba conversion for alpha_composite
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        new_im = Image.new(im.mode, (width, height))
        # alpha_composite required over paste
        new_im.alpha_composite(im, (woffset, hoffset))
        return new_im

    def txt2image(txt, font, width, height, fillcolor, xoffset, yoffset):
        im = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(im)
        draw.text((xoffset, yoffset), txt, font=font, fill=fillcolor)
        return im
    
    def sendImageFile(client, layer, file, width, height, once):
        anim_cache = None
        with Image.open(file) as im:
            if hasattr(im, 'is_animated') and im.is_animated:
                # just fill the cache
                anim_cache = []
                for n in range(im.n_frames):
                    anim_cache.append({ "img": DmdPlayer.imageConvert(DmdPlayer.imageFit(im, width, height)), "duration": im.info["duration"] })
                    im.seek(n)
            else:
                im = DmdPlayer.imageFit(im, width, height)
                DmdPlayer.sendFrame(client, layer, DmdPlayer.imageConvert(im))
        # close the image to just play the cache
        if anim_cache is not None:
            DmdPlayer.playAnim(client, layer, anim_cache, once)

    def sendVideoFile(client, layer, file, width, height, once):
        while True:
          cap = cv2.VideoCapture(file)
          fps = cap.get(cv2.CAP_PROP_FPS)
          last_rendering = None
          print("fps:{}".format(fps))
          nskip = fps // 20 # skip some frames to not overload too much ; no more than 20 fps
          f = 0
          while(cap.isOpened()):
            ret, cv2_im = cap.read()
            if not ret:
                break;
            if f % nskip == 0:
                im = Image.fromarray(cv2.cvtColor(cv2_im, cv2.COLOR_BGR2RGB))
                im = DmdPlayer.imageFit(im, width, height)
                DmdPlayer.sendFrame(client, layer, DmdPlayer.imageConvert(im))
            if last_rendering is not None:
                now = time.time()
                d = now - last_rendering;
                if d < 1/fps:
                    time.sleep(1/fps - d)
            last_rendering = time.time()
            f +=1
          cap.release()
          if once:
              break

    def playAnim(client, layer, anim, once):
        while True:
            for n in range(len(anim)):
                ts = time.time()
                DmdPlayer.sendFrame(client, layer, anim[n]["img"])
                # just to a more homogene animation by removing time lost in system calls
                spent_ts = time.time() - ts
                delay = anim[n]["duration"]/1000
                if(delay > spent_ts):
                    time.sleep(delay - spent_ts)
            if once:
                break

    def sendText(client, layer, text, color, target_width, target_height, fontfile, moving_text, fixed_text, speed, once):
        font = ImageFont.truetype(fontfile, target_height)
        (left, top, right, bottom) = font.getbbox(text)
        img_width  = right - left
        img_height = bottom - top
        fit = img_width < target_width
        if fit and (moving_text is not True or fixed_text is True): # the text fit on the screen
            im = DmdPlayer.txt2image(text, font, img_width, img_height, color, 0, -top)
            im = DmdPlayer.imageFit(im, target_width, target_height) # an optimisation could be to directly fit with an extra argument in txt2image
            DmdPlayer.sendFrame(client, layer, DmdPlayer.imageConvert(im))
        elif not fit and (moving_text is not True or fixed_text is True): # it doesn't fix, resize
            im = DmdPlayer.txt2image(text, font, img_width, img_height, color, 0, -top)
            im = im.resize((target_width, img_height * target_width // img_width))
            im = DmdPlayer.imageFit(im, target_width, target_height) # an optimisation could be to directly fit with an extra argument in txt2image
            DmdPlayer.sendFrame(client, layer, DmdPlayer.imageConvert(im))
        else:
            # move the text ; generate all the frames in a cache first
            anim_cache = []
            im = DmdPlayer.txt2image(text, font, img_width, img_height, color, 0, -top)
            im = DmdPlayer.imageFit(im, target_width, target_height, False)
            reswidth, resimg_height = im.size
            for i in range(1, target_width+reswidth):
                new_im = Image.new('RGB', (target_width, target_height))
                new_im.paste(im, (target_width-i, 0))
                anim_cache.append({ "img": DmdPlayer.imageConvert(new_im), "duration": speed })
            DmdPlayer.playAnim(client, layer, anim_cache, once)

    def getServerInfos(client):
        # todo : protocol : see https://docs.python.org/3/howto/sockets.html#socket-howto
        while True:
            x = client.recv(4096)
            if len(x) == 0:
                raise Exception("connection lost")
            data = x.decode("utf-8")
            p = re.compile("^([0-9]*)x([0-9]*)$")
            for l in data.splitlines():
                res = p.search(l)
                if res is not None:
                    return {"width": int(res.group(1)), "height": int(res.group(2))}

    def run(feature_video):
        parser = argparse.ArgumentParser(prog="dmd-play")
        parser.add_argument("-f", "--file", help="image path file")
        if feature_video:
            parser.add_argument("-v", "--video", help="video path file")
        parser.add_argument("-t", "--text", help="text")
        parser.add_argument("--font", default="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", help="path to the font file")
        parser.add_argument("--clear", action="store_true",          help="clear the screen")
        parser.add_argument("--overlay", action="store_true",        help="restore the previous frames once finished")
        parser.add_argument("--overlay-time", type=int, default=1000,  help="time to pause fixed images for the overlay in ms")
        parser.add_argument("--moving-text", action="store_true",    help="always makes the text to move, even if text fits")
        parser.add_argument("--fixed-text",  action="store_true",    help="never makes the text to move, prefer to adjust size")
        parser.add_argument("-r", "--red",   type=int, default=255,  help="red text color")
        parser.add_argument("-g", "--green", type=int, default=0,    help="green text color")
        parser.add_argument("-b", "--blue",  type=int, default=0,    help="blue text color")
        parser.add_argument("-s", "--speed", type=int, default=20,   help="sleep time during each text position (in milliseconds)")
        parser.add_argument("--once",  action="store_true",          help="don't loop forever")
        parser.add_argument("-p", "--port", type=int, default=53533, help="network connexion port")
        parser.add_argument("--host", default="localhost",           help="dmd server host")
        args = parser.parse_args()

        allNone = True
        if args.file is not None:
            allNone = False
        if args.text is not None:
            allNone = False
        if feature_video and args.video is not None:
            allNone = False
        if args.clear is True:
            allNone = False

        if allNone:
            sys.stderr.write("Missing something to play\n")
            return

        # connect
        layer = "main"
        if args.overlay:
            layer = "overlay"
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((socket.gethostbyname(args.host), args.port))
        srv = DmdPlayer.getServerInfos(client)
        print("server: {}x{}".format(srv["width"], srv["height"]))
        if args.file:
            DmdPlayer.sendImageFile(client, layer, args.file, srv["width"], srv["height"], args.once)
        elif args.text:
            DmdPlayer.sendText(client, layer, args.text, (args.red, args.green, args.blue), srv["width"], srv["height"], args.font, args.moving_text, args.fixed_text, args.speed, args.once)
        elif feature_video and args.video:
            DmdPlayer.sendVideoFile(client, layer, args.video, srv["width"], srv["height"], args.once)
        elif args.clear:
            DmdPlayer.sendText(client, layer, "", (args.red, args.green, args.blue), srv["width"], srv["height"], args.font, False, True, args.speed, True)

        if args.overlay:
            time.sleep(args.overlay_time/1000)

if __name__ == '__main__':
    DmdPlayer.run(feature_video)
