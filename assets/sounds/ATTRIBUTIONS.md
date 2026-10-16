<!--
SPDX-FileCopyrightText: Simon A. F. Lund <os@safl.dk>

SPDX-License-Identifier: BSD-3-Clause
-->

# Sound asset attributions

This file records the source, author, license, and any modifications for every
sound shipped under `assets/sounds/`. Most of the files here are public domain
or CC0 (no attribution required) — we record them anyway because crediting
people who release work for free is the right thing to do, and because anyone
later reusing this library should be able to trace each sample back to its
origin without re-deriving the chain.

If you add a sound, add a row. If a row's source URL goes 404 someday, the
filename, author, and license still tell you whether you can keep using the
sample.

## Format

Each entry lists:

- **File** — path relative to this directory.
- **Source** — original page (not the direct download URL — those rot faster).
- **Author** — who recorded it.
- **License** — exact licence text, not paraphrased.
- **Modifications** — anything the smashbox build did to the original file
  (trim, transcode, rename). "None" if the file is shipped verbatim.

## Animals

### `animals/bird.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Bird_singing.ogg
- **Author:** "jc" (uploaded to PDSounds, re-hosted on Wikimedia Commons)
- **License:** Public domain — released worldwide by the author via PDSounds
  ("no copyrights, no strings attached").
- **Modifications:** Trimmed to the first 3.0 s with ffmpeg
  (`-ss 0 -t 3.0 -c:a libvorbis -q:a 5`). Original was 62.6 s of continuous
  garden birdsong.

### `animals/cat.ogg`

- **Source:** https://opengameart.org/content/cat-purr-meow
- **Author:** OpenGameArt user (per the upload page; the bundle ships
  under CC0).
- **License:** CC0 1.0 (public-domain dedication; attribution not required).
- **Modifications:** Renamed from `cat_mewpurr.wav` to `cat.ogg`,
  resampled to 48 kHz mono and encoded as Ogg Vorbis (the kiosk's Pi 4
  HDMI mixer ends up at 48 kHz; pre-resampling avoids any SDL_mixer
  on-load conversion artefacts that made the previous 44.1 kHz
  `cat_softmew.wav` sound thin / sped-up).

### `animals/cow.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Mudchute_cow_1.ogg
- **Author:** Wikimedia Commons user "Secretlondon"
  (Mudchute City Farm, London, recorded April 2007)
- **License:** Multi-licensed — GFDL v1.2+ **and** CC-BY-SA 3.0 / 2.5 / 2.0 / 1.0.
  Practically: free to use anywhere with attribution; share-alike applies to
  *modifications of the audio*, not to projects that embed it as data.
- **Modifications:** Renamed from `Mudchute_cow_1.ogg` to `cow.ogg`. Audio
  unchanged (2.2 s as recorded).

### `animals/dog.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Barking_of_a_dog.ogg
- **Author:** Wikimedia Commons uploader
- **License:** CC-BY-SA 3.0 Unported. **Attribution required**: "Barking of
  a dog, Wikimedia Commons, CC-BY-SA 3.0". Share-alike applies to
  modifications of the audio, not to projects embedding it as data.
- **Modifications:** Single-pass ffmpeg encoding: resampled to 48 kHz
  mono, full 2.55 s used, 5 ms fade-in + 20 ms fade-out, Ogg Vorbis
  quality 7. Replaces the earlier PD `Dog.ogg` from PDSounds (the
  trimmed first 2.5 s of that 84 s recording didn't read as a clear
  bark on the kiosk).

### `animals/frog.ogg`

- **Source:** https://opengameart.org/content/ribbit-frog-sounds (file
  `ribbit_01.mp3` in the bundle by EZduzziteh).
- **Author:** EZduzziteh (OpenGameArt).
- **License:** CC0 1.0 (public-domain dedication; attribution not required).
- **Modifications:** Single-pass ffmpeg encoding: decoded from MP3,
  resampled to 48 kHz mono, full 1.21 s used, 5 ms fade-in +
  20 ms fade-out, Ogg Vorbis quality 7. Replaces the earlier PD pig-frog
  recording (the pig frog grunt is a real frog sound but didn't read as
  "frog" to a toddler ear).

### `animals/owl.ogg`

- **Source:**
  https://commons.wikimedia.org/wiki/File:Tawny_Owl_(Strix_aluco)_(W1CDR0001519_BD8).ogg
- **Author:** British Library Sounds (recorded 1978-01-18, Edgeworth,
  Gloucestershire, England)
- **License:** CC-BY-SA 4.0. **Attribution required**: "British Library
  Sounds, recording W1CDR0001519 BD8, CC-BY-SA 4.0". Share-alike applies to
  modifications of the audio, not to projects embedding it as data.
- **Modifications:** Cropped to the 2.5 s window starting at 1.0 s of the
  original (the first clear hoot — silence detection at -30 dB located it).
  Re-encoded to Ogg Vorbis quality 5.

### `animals/pig.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Pig_grunt_-_Erdie.ogg
- **Author:** Wikimedia Commons user "Erdie"
- **License:** CC-BY 3.0. **Attribution required**: "Pig grunt by Erdie,
  CC-BY 3.0".
- **Modifications:** Renamed from `Pig_grunt_-_Erdie.ogg` to `pig.ogg`.
  Audio unchanged (0.7 s as recorded).

### `animals/sheep.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Sheep_bleat.ogg
- **Author:** Wikimedia Commons uploader (per the file's CC0 dedication)
- **License:** CC0 1.0 (public-domain dedication; attribution not required).
- **Modifications:** Renamed from `Sheep_bleat.ogg` to `sheep.ogg`. Audio
  unchanged (0.5 s as recorded).

## Instruments

### `instruments/xylophone-1.ogg`, `instruments/xylophone-2.ogg`

- **Source:** https://opengameart.org/content/childrens-xylophone
- **Author:** OpenGameArt user (per the upload page); audio packaged with a
  CC0 1.0 LICENSE.txt
- **License:** CC0 1.0 (public-domain dedication; attribution not required).
- **Modifications:** Renamed from `child's_xylophone-{1,2}.ogg` (dropping the
  apostrophe to keep filenames shell-safe). The `.wav` versions in the
  upstream bundle were dropped — pygame.mixer plays the `.ogg` files directly
  and they're ~10× smaller.

### `instruments/guitar.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:AcousticGuitarSample.ogg
- **Author:** Wikimedia Commons uploader (per the file's public-domain
  declaration: "I grant anyone the right to use this work for any purpose,
  without any conditions").
- **License:** Public domain.
- **Modifications:** Trimmed to the first 2.5 s with ffmpeg
  (`-ss 0 -t 2.5 -c:a libvorbis -q:a 5`). Original was 48.9 s of mixed
  guitar demonstration.

### `instruments/piano.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Piano.ogg
- **Author:** Wikimedia Commons uploader
- **License:** Dual-licensed under CC-BY-SA 3.0 Unported and GFDL v1.2+.
  **Attribution required**. Share-alike applies to *modifications of the
  audio*, not to projects embedding it as data.
- **Modifications:** Cropped to the 1.3 s window starting at 0.25 s
  (skipping the leading silence detected at -30 dB) and re-encoded to Ogg
  Vorbis quality 5.

### `instruments/saxophone.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Jazz-Sax.ogg
- **Author:** "Serolillo" (per the file page)
- **License:** CC-BY 2.5 Generic. **Attribution required**: "Jazz tenor
  sax sample by Serolillo, CC-BY 2.5".
- **Modifications:** Cropped to a 2.3 s window starting at 4.4 s (the
  original opens with ~4.3 s of silence before the first sax phrase).
  Re-encoded to Ogg Vorbis quality 5.

### `instruments/flute.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Flute.ogg
- **Author:** "hokuspokus" (uploaded to PDSounds; re-hosted on Wikimedia
  Commons).
- **License:** Public domain — released worldwide by the author via
  PDSounds.
- **Modifications:** Trimmed to the first 2.5 s with ffmpeg. Original was
  13.1 s of sustained flute tone.

### `instruments/violin.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Violin_pizzicato.ogg
- **Author:** Wikimedia Commons uploader
- **License:** Dual-licensed under CC-BY-SA 3.0 Unported and GFDL v1.2+.
  **Attribution required**.
- **Modifications:** Renamed from `Violin_pizzicato.ogg` to `violin.ogg`.
  Audio unchanged (4.8 s as recorded).

### `instruments/drums.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Snare_drum_unmuffled.ogg
- **Author:** Wikimedia Commons uploader (March 2005 recording)
- **License:** Dual-licensed under CC-BY-SA 3.0 Unported and GFDL v1.2+.
  **Attribution required**.
- **Modifications:** Cropped to the 0.8 s window starting at 0.45 s
  (isolating one clean snare hit out of the original 4.8 s recording).
  Re-encoded to Ogg Vorbis quality 5.

### `instruments/percussion.ogg`

- **Source:** https://commons.wikimedia.org/wiki/File:Hi_hat_closed.ogg
- **Author:** Wikimedia Commons user "Clngre" (March 2005 recording)
- **License:** Dual-licensed under CC-BY-SA 3.0 Unported and GFDL v1.2+.
  **Attribution required**. Share-alike applies to *modifications of the
  audio*, not to projects embedding it as data.
- **Modifications:** Cropped to the first 0.7 s (isolating the strike +
  natural decay from the 4.0 s original), downsampled to 48 kHz mono
  and re-encoded to Ogg Vorbis quality 5. **Previous file** was a
  PD wood-knock from PDSounds; replaced because the `_clip_percussion`
  sprite shows hi-hat cymbals and the audio/visual mismatch was
  confusing.
