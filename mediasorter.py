#!/usr/bin/env python 
import os, re, subprocess, fnmatch, shlex, time, argparse, string
from datetime import datetime,timedelta
from string import Template

# Script overview
# Purpose: Sort downloaded content into a structure friendly to TV browsing, as well as iTunes mapping.
# Optionally fetches new content from a download folder (e.g. the destination of torrents)

# Environment: Downloads and video dirs on local or mounted directory. Works well with Qnap NAS.

# Principle: First, all folder trees containing videos of right extension are moved from download
# directory to normal video directory. Videos optionally sorted by codec type (as different players
# have different capabilities). Goes through video directory to tidy up. Will assume each subdir tree
# belongs to the same video set - e.g. one movie, TV series, or similar. The script will:

# 1) Collapse empty dirs to shorten tree depth (less clicking)
# 2) Rename folder and file name to most descriptive, attempting to preserve original release name
# 3) Hides unecessary files (.fastresume, .nfo, .jpg, samples, etc) in meta directory to not clutter view (optionally deletes)
# 4) Downloads subtitles for all videos if missing
# Handles all video formats including DVD images. Handles many different directory structures or lack of structure. Can do efficient file
# moving if working on network drive (by sshing and doing a mv, rather than mv across volumes)

# What the script DOESN'T yet do:
# - No metadata fetching except subtitles (most TV boxes can do it themselves).
# - Can't handle completely flat structures (all video files in the same folder, no subdirs)
# - Can't differentiate video types, such as separating TV from Movie from Youtube clips.

# Types of video dirs
#
# src_dir/                     |Action| Rename video.ext to video_dir.ext if "better" than video
#  video_dir/ <-- basic case
#    video.ext
#
# src_dir/                     |Action| Collapse top dir into subdir and remove .fastresume
#   video>
#     video.fastresume <-- torrent directory on top
#     video/
#       video.ext
#
# src_dir/                     |Action| Treat VIDEO_TS and AUDIO_TS as video files, do not go deeper
#   video/
#     VIDEO_TS/ <-- a disk image - the VIDEO_TS folder should be treated as if it was a file
#       a.vob 
#       b.vob
#
# src_dir/                     |Action| Unrar, keep video, delete rars
#   video/
#     video.r01 <-- one video split in many rars
#     video.r02
#
# src_dir/                     |Action| For each video, rename to "better" if needed
#  video/
#    video_part1.ext <-- several parts of same video, can be CD1, CD2 as well
#    video_part2.ext
#    video_part3.ext
#
# src_dir/                     |Action| For each video,
#  video/
#    video_ep1.ext <-- different episodes of serial
#    video_ep2.ext
#
# src_dir/
#   video/
#     CD1/
#       video.ext
#     CD2/
#       video.ext
#
# src_dir/
#   many_videos/
#     videoA.ext  <-- completely different video files in same dir, should go to different places
#     videoB.ext
#
# src_dir/
#   video.ext <-- video in root, need to be put in folder
#
# src_dir/
#  video/
#    ..  <-- empty directory, should be removed
#
# src_dir/
#   video/
#     video.ext
#     Subs/
#     Sample/
#     video.nfo  <-- not needed
#     video.srt  <-- needed
#     video.sub  <-- needed
#     video.idx  <-- needed
#     video.txt  <-- not needed
#     video.jpg  <-- not needed
#     video.png  <-- not needed
#     video.sfv  <-- not needed
#     video_SAMPLE.ext <-- not needed, can have different names
#     video.fastresume <-- not needed

###### COMMAND LINE ARGS ###############################################
## Options available through command line
format_keys = {
  'mediatype':    "$mediatype     [video, music, pictures]",
  'filetype':     "$filetype      [Divx, Mkv, Mp4, Others]",
  'ext':          "$ext           [mkv, divx, avi, mpg, mp4, m4v, iso, vob, img]",
  'video_codec':  "$video_codec   h264, x264, h.264, XviD, DivX, XvidHD, mpeg2, avc",
  'sound_codec':  "$sound_codec   Mp3 AC3 DTS DD5.1 DD6.1 DD7.1 AAC AC-3 5.1 6.1 7.1",
  'year':         "$year          [...]",
  'rip':          "$rip           DvdRip DVDRip DVD DVDSCR Screener BRRip BluRay HDTV HDrip vhsrip VHS",
  'resolution':   "$resolution    720p, 1080p, WS, DVD5, HD",
  'part':         "$part          vol 1, vol 2, cd1, cd2, s01e02",
  'title':        "$title         [...]",
  'lang':         "$lang          Languages or subtitles listed for the media",
  'torrent':      "$torrent       Torrent source or tracker",
  'filename':     "$filename      Original file name as is",
  'mediaroot':    "$mediaroot     The original unique path to this file, e.g. don't change paths",
  'discarded':    "$discarded     Discarded words from original file"
  }

default_noimport = ["apps", "Bastardo", "games", "macapps"] 
default_subs = ""
default_unfinished_torrents = ["/mnt/ext/home/httpd/cgi-bin/Qdownload/pause.org", "/mnt/ext/home/httpd/cgi-bin/Qdownload/refresh.org"]
default_exclude = ["Own", "Own Videos", "Series", "Trailers and Shortfilms", "Youtube", "_Recent"]
default_deletefiles = ['*fastresume','*.sfv', '._*', 'Thumbs.db']
default_keepfiles = ['*.bup','*.ifo', '.ds_store'] # Should be lowercase
default_format = '$filetype/$title ($year)/$filename'

parser = argparse.ArgumentParser(description="Sort your media library", version=0.2, 
  formatter_class=argparse.RawDescriptionHelpFormatter,
  epilog='Format keys:\n'+'\n'.join(format_keys.values()))
parser.add_argument('media_dir',
  help='the directory to sort')

parser.add_argument('-f','--format', default=default_format,
  help='the desired format of directories and file names')
parser.add_argument('-i','--import', metavar='DIR', dest='import_dirs', action='append',
  help='a directory to import media from before sorting (repeatable option)')
parser.add_argument('-n', '--noimport', metavar='*', action='append', default=default_noimport,
  help='absolute paths or file/directory patterns to exclude from import'+
    ' e.g. "/some/path" or "*.mkv" or "apps/" (repeatable option)')
parser.add_argument('-t', '--unfinished_torrents', metavar='FILE', action='append', default=default_unfinished_torrents,
  help='paths to files listing torrent files or dirs that are currently downloading and should be excluded (repeatable option)')
parser.add_argument('-e', '--exclude', metavar='*', action='append', default=default_exclude,
  help='absolute paths or file/directory patterns to exclude from sorting'+
    ' e.g. "/some/path" or "*.mkv" or "apps/" (repeatable option)')
parser.add_argument('-x', '--execute', default=False, action='store_true',
  help='executes file operations instead of just showing them - TAKE CARE AND REVIEW FIRST')
parser.add_argument('-b', '--batch', default=False, action='store_true',
  help='do NOT prompt for each file operation (e.g. for automated execution)')
parser.add_argument('--subs', default=default_subs,
  help='two letter code for subtitle languages to look for, e.g. "en"')
parser.add_argument('--keepfiles', default=default_keepfiles,
  help='file matching patterns (e.g. "*.ext") for files to always keep together with mediafiles')
parser.add_argument('--deletefiles', default=default_deletefiles,
  help='file matching patterns (e.g. "*.ext") for files to always keep DELETE')

args = parser.parse_args()
print args

###### CONFIG ###############################################
## Detailed configuration not accessible through command line

#TODO

# Musa the warrior__korean --> korean not put as language, same for Juno (2007) English
# Keep SSH stream open
# Seij gakuen has unicode that is unclear if it work
# 3 Idiots RAR file not unpacked, incorrectly sent to metadata
# Unpack RAR, confirm ok, delete RAR, give option
# Save original paths to restore file
# Add to iTunes after import
# Empty metadatafolders will not be removed currently
# If there are subs in Subs-folder or any other subfolder, it would not be used and would instead 
# be sent to metadata folder
# Symlink folders that store all files of certain type
# Package as python egg with install script
# In Other, when there are videos in the root, it ignores the subfolders (and treats Other as a media dir)
# THE NAKED GUN 33 is all caps

divx_subdir = "Divx"
mkv_subdir = "Mkv-Etc"
h264_subdir = "h264"
dvd_subdir = "DVD"
other_subdir = "Other"

video_types = {   'mkv':mkv_subdir,
                  'divx':divx_subdir,
                  'avi':divx_subdir,
                  'mpg':divx_subdir,
                  'mp4':h264_subdir,
                  'm4v':h264_subdir,
                  'iso':dvd_subdir,
                  'vob':dvd_subdir,
                  'img':dvd_subdir,
                  'VIDEO_TS':dvd_subdir,
                  'wmv': other_subdir,
                  'ogm': other_subdir}
meta_dir_name = "metadata"
subfiles_ext = ['srt', 'sub', 'idx'] # sub extensions
find_subtitles =  ['mkv','divx','avi','mp4','m4v'] # file extensions of files to attempt search for subtitle

# if working on external machine, fill with this variable with user@domain
# The SSH destination needs to be pre-authenticated, see http://linuxproblem.org/art_9.html
ssh_string = "ssh admin@192.168.0.50"
remote_path_replace = ("/Volumes","/share")
ssh_process = None

commands = {
  'move': {'cmd': 'mv', 'name': 'Move', 'remote':True},
  'delete': {'cmd': 'rm', 'name': 'Delete', 'remote':True},
  'delete_dir': {'cmd': 'rm -R', 'name': 'Delete dir'},
  'make_dir': {'cmd': 'mkdir', 'name': 'Make dir'},
  'make_path': {'cmd': 'mkdir -p', 'name': 'Make whole path'},
  'search_subs': {'cmd': 'periscope -l '+args.subs, 'name': 'Search for subtitles'},
  'output': {'cmd': 'cat', 'name': 'Search for subtitles', 'remote':True},
  'list_rar': {'cmd': 'unrar lb', 'name': 'List contents of RAR'},
  'unrar': {'cmd': 'unrar e -o-', 'name': 'UnRAR'},
}

move_cmd = {
    'cmd':    ssh_string+' "'+'mv%s"',
    'path':   ' \\"%s\\"',
    'replace':remote_path_replace,
    'name': 'Move'}
rmdir_cmd = {
    'cmd':    ssh_string+' "'+'rm -R%s"',
    'path':   ' \\"%s\\"',
    'replace':remote_path_replace,
    'name': 'Remove dir'}
rm_cmd = {
    'cmd':    'rm%s',
    'path':   ' "%s"',
    'name': 'Remove'}
mkdir_cmd = {
    'cmd':    'mkdir%s',
    'path':   ' "%s"',
    'name': 'Make dir'}
mkdir_rec_cmd = {
    'cmd':    'mkdir -p%s',
    'path':   ' "%s"',
    'name': 'Make whole path'}
periscope_cmd = {
    'cmd':    'periscope -l '+args.subs+'%s',
    'path':   ' "%s"',
    'name': 'Look for subs to'}
output_cmd = {
    'cmd':    ssh_string+' "'+'cat%s"',
    'path':   ' \\"%s\\"',
    'name': 'Display '}
rarlist_cmd = {
    'cmd':    'unrar lb%s', #lb means list bare, only file names
    'path':   ' "%s"',
    'name': 'List in RAR'}
unrar_cmd = {
    'cmd':    'unrar e -o-%s', #extract, do NOT overwrite
    'path':   ' "%s" ',
    'name': 'UnRAR'}
global cmds, cmds_history, reverse_cmds, created_paths
created_paths = set()
reverse_cmds = dict()

def human_friendly_cmd(cmd, *paths):
  if len(paths)>1:
    # Find common prefix path, but make sure it only goes up to last separator
    # otherwise test/Dallas and test/Danube would have common path "test/Da"
    common_root = os.path.commonprefix(paths).rsplit(os.path.sep,1)[0]
    print_paths = [p.replace(common_root,"") for p in paths]
  else:
    print_paths = paths
  if len(paths)>2:
    common = os.path.commonprefix(print_paths[:-1]).rsplit(os.path.sep,1)[0]
    return '%s "%s" --> "%s"' % (cmd['name'], '","'.join([p.replace(common,"") for p in print_paths[:-1]]), print_paths[-1]) 
  else:
    return '%s "%s"' % (cmd['name'], '" --> "'.join(print_paths))

def queue_cmd(cmd, *paths):
  print human_friendly_cmd(cmd, *paths)
  global cmds
  paths_merged =""
  for path in paths:
    if('replace' in cmd): #we have a replace component, means we need to replace in path
      path = path.replace(cmd['replace'][0],cmd['replace'][1], 1) # max 1 replacement to ensure we replace beginning of path 
    paths_merged = paths_merged + (cmd['path'] % path)
  cmds.append(cmd['cmd'] % paths_merged)
#####################################################

def save_reverse_cmd(fromdir, fromfile, todir, tofile):
  global reverse_cmds
  frompath = os.path.join(fromdir,fromfile)
  topath = os.path.join(todir, tofile)
  print "Reverse '%s' '%s'" % (topath, frompath)
  # Have current frompath previously been stored, if so update it's "topath" to new
  # path given but keep original frompath
  if frompath in reverse_cmds:
    op,of,np,nf = reverse_cmds[frompath]
    del reverse_cmds[frompath]
    reverse_cmds[topath] = (op,of,todir,tofile)
  else: # Not found, file must be new, but save frompath and topath for later
    reverse_cmds[topath] = (fromdir,fromfile,todir,tofile)
    
def move(fromdir, fromfile, todir, tofile="", all=False):
  global created_paths
  topath = os.path.join(todir, tofile)
  paths = []
  if type(fromfile) is list:
    if tofile is not "":
      raise Exception("To file %s must be empty when having multiple from files" % tofile) 
    for f in fromfile:
      frompath = os.path.join(fromdir, f)
      paths.append(frompath)
      #save_reverse_cmd(fromdir, f, todir, f)
    if all:
      paths = [os.path.join(fromdir, '*')] # actually erase paths and add a wildcard to save time
    paths.append(os.path.join(todir,tofile)) 
  else:
    frompath = os.path.join(fromdir, fromfile)
    #save_reverse_cmd(fromdir, fromfile, todir, tofile)
    paths = [frompath, topath]
  
  if todir not in created_paths and not os.path.exists(todir):
    queue_cmd(mkdir_rec_cmd, todir)
    created_paths.add(todir)
  queue_cmd(move_cmd, *paths)
  
def init_cmds():
  global cmds, cmds_history
  cmds = []
  cmds_history = set()
#####################################################

def run_cmd(cmd, execute=False):
  do_cmd = True
  if not execute and not args.batch:
    print "[y/n]? %s" % cmd
    input = raw_input()
    if not input.startswith("y"):
      do_cmd = False
      print "Ignored!"
  output = ""
  retcode = -1
  if do_cmd and (args.execute or execute):
    if cmd['cmd'].startswith(ssh_string):
      if not ssh_process:
        ssh_process = subprocess.Popen(shlex.split(ssh_string), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
      ssh_process.stdin.write(
    else:
      sub = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE)
      output = sub.communicate()[0]
      retcode = sub.returncode
  return output, retcode
#####################################################

def pop_cmd(execute=False):
  cmd = cmds.pop()
  if cmd in cmds_history:
    print "Already run before, ignored: %s" % cmd
    output = ""
    retcode = -1
  else:
    output, retcode = run_cmd(cmd,execute)
    if retcode is not -1: # If return -1 it means the command was not run
      cmds_history.add(cmd)
  return output, retcode
#####################################################
  
def flush_cmds():
  for cmd in cmds:
    if cmd in cmds_history:
      print "Already run before, ignored: %s" % cmd
    else:
      run_cmd(cmd)
    cmds_history.add(cmd) # Remember that we processed this cmd
#####################################################

init_cmds()

### Make sure we are configured correctly
if not os.path.isdir(args.media_dir):
    exit("Media path %s is not a directory or is inaccessible, check volume mounts or give different path" % args.media_dir)

args.media_dir = os.path.normpath(args.media_dir) # Remove ending slashes if any

if args.import_dirs:
  for i, d in enumerate(args.import_dirs):
    if not os.path.isdir(d):
      exit("Import path %s is not a directory or is inaccessible, check volume mounts or give different path" % d)
    common = os.path.commonprefix([d,args.media_dir])
    if common==d or common==args.media_dir:
      exit("Either media dir %s or import %s dir is a subdirectory of the other, which is not allowed" % (args.media_dir, d))
    args.import_dirs[i] = os.path.normpath(d) # Remove ending slashes if any
  if len(args.import_dirs)>1:
    sorted = sorted(args.import_dirs)
    for i, d in enumerate(sorted[1:]):
      common = os.path.commonprefix([d,sorted[i-1]])
      if common==d or common==sorted[i-1]:
        exit("Either one of import dirs %s and %s is a subdirectory of the other, which is not allowed" % (d, sorted[i-1])) 

noimport_filters = []
if args.noimport:
  tmp = args.noimport
  args.noimport = []
  for i, e in enumerate(tmp):
    if e.startswith(os.path.sep): # An absolute path
      e = os.path.normpath(e)  # Remove ending slashes if any
      args.noimport.append(e)
    else: #Interpret as an exclude pattern
      noimport_filters.append(e)

# Make sure format is valid
# $filetype/$title ($year)/$filename
for m in re.finditer(r"$(\w+)",args.format):
  if m.group(1) not in format_keys:
    exit("Key "+m.group(0)+" in format %s is not a recognized key" % args.format)
if args.format.startswith("/"):
  exit("Format %s cannot begin with /, e.g. the path is relative from %s!" % (args.format, args.media_dir))
if '$filename' not in args.format and '$ext' not in args.format:
  exit("Format lacks $ext and $filename, need to have either one")
format_parts = args.format.count("/")
if format_parts==0: # means no folder path was given, e.g. 
  exit("Format %s need to have at least one folder in the path!" % args.format)
title_i = args.format.find("$title")
if title_i==-1:
  exit("No $title format found in input format - you need to sort at least by title")
part_i = args.format.find("$part")
if part_i>=0:
  if part_i<title_i:
    exit("$part format cannot be before $title")
elif args.format.find("$filename")==-1: # no part and no filename in format
  exit("No $part in format and not using $filename is not allowed (because may overwrite part files)")
cleaned = args.format.translate(None, '\?*:|"<>')
if len(cleaned) < len(args.format):
  print("Format "+args.format+" had the following illegal characters removed " + args.format.translate(None, cleaned))
  args.format = cleaned

args.keepfiles = [f.lower() for f in args.keepfiles]
args.deletefiles = [f.lower() for f in args.deletefiles]

chosen_format_keys = [k for k in format_keys if k in args.format]

# Read files containing the dir/file names of what is already being downloaded, and print into variable
# We don't want to move files still under download. Can also add file listing what to ignore here.
files_downloading = ""
if args.unfinished_torrents:
  queue_cmd(output_cmd, *args.unfinished_torrents) # Use cat on all files to read into variable
  files_downloading,retcode = pop_cmd(True)
  if retcode is not -1:
    print "Currently downloading: " + files_downloading
  else:
    print "Could not read downloading torrents"

# Matches .x264, h264, xvid, divx, etc at the end of string or a secion
match_video = re.compile(r"[_\W](?P<val>[xh]\.?264|xvidhd|xvid|divx|mpeg2|avc)($|[_\W])", re.I) # case insensitive
# Matches four digits in between some section breaker (e.g. moviename-2007- or moviename[1998])
match_year = re.compile(r"[_\W](?P<val>\d{4})($|[_\W])")
# Matches audio acronyms
match_sound = re.compile(r"[_\W](?P<val>(\d(.1|Ch)[_\W]?)?(mp3|ac3|dts|dd|aac|ac-3)([_\W]?\d(.1|Ch)[_\W]?)?)($|[_\W])", re.I)
# Matches type of rip acronyms
match_rip = re.compile(r"[_\W](?P<val>dvdrip|dvdscr|dvd|screener|scr|brrip|bdrip|bluray|hdtv|hdtvrip|hdrip|vhsrip|vhs|vod|vodrip)($|[_\W])", re.I)
match_resolution = re.compile(r"[_\W](?P<val>((\d{3,4}x)?(720|1080)p?)|ws|hd)($|[_\W])", re.I)
match_lang = re.compile(r"[_\W](?P<val>(english|eng|en|swedish|sv|swe|french|korean|nl|hindi)([_\W]?(subtitles|subbed|subs|sub))?)($|[_\W])", re.I)
# Matches movies split by disk, part, etc, can be followed by up to 2 digits, e.g. Part 02, CD1, A
match_part = re.compile(r"(^|[_\W])(?P<val>(cd|part|pt|episode|ep|s\d{1,2}e|vol)([._\W]?(\d{1,2}|[iv]+))?)($|[_\W])", re.I)
match_part_alt = re.compile(r"[_\W](?P<val>a|b|\d{1,2})(.#|$)", re.I)
match_extension = re.compile(r"(^|[_\W])(?P<val>(mkv|divx|avi|mpg|m4v|mp4|iso|vob|VIDEO_TS|wmv|ogm))$", re.I)
# Matches a name between () [] or {}, alternatively ending in 1231512.TPB (or some other digits)
#match_torrent = re.compile(r"(?P<val>([]()[{}][^\]()\[{}]+[]()[{}])|\d{4,}\.TPB$)", re.I)
match_torrent = re.compile(r"[_\W]*(?P<val>(demonoid|kat|isohunt|mininova|\d{6,}|www[_\W]\w+)([_\W]\w{2,4})?)[_\W]*", re.I)
#Match from beginning until a replacement was made or 3 consecutive non-word chars. Use non-greedy match.
# If it should end with 3+ non-word chars, we exclude "-" because it can appear in middle of title
match_title = re.compile(r"^[_\W]*(?P<val>.{3,}?[])]?)([_\W]*#|[_\W]*$)", re.I)
match_split = re.compile(r"[. _#]{2,}|[]()[{}]")

# Matches left-over characters
# To keep 'word, word', 'word 2.5', 'word - word', 'word's'
# To remove 'word.word', 'word_word', 'word-', 'word.', 'word....word'. '-word'

# uses negative lookbehind (?<!..) and lookahead (?!...) to only match "." not between ' /d' and ' /d'
match_clean1 = re.compile(r"(?<! \d)(?P<val>\.+)(?!\d )")
match_clean2 = re.compile(r"(?P<val>[_# ]+)")
match_clean3 = re.compile(r"(?P<val> *-+ *)")

match_correctcase = re.compile(r"[A-Z][a-z]")
match_unfilled_format_keys = re.compile(r"[-._ ]+[{([]? ?\$\$ ?[)}\]]?")

#TODO search for only approved media extensions, compile regex from extension list
match_fileext_in_results = re.compile(r"\.(\w{3,4})$", re.I)
#####################################################

def match_remove(reobj, str, metadata=None, key=None, max=1, replace='#'):
    if metadata and key and key not in metadata:
      raise Exception("Incorrect key '%s' for metadata" % key) 
    matches = reobj.finditer(str)
    i = 0
    offset = 0
    for m in matches:
      if m and m.group('val'):
        #print "Found '%s' in '%s' using %s" % (m.group('val'), str, reobj.pattern)
        if metadata and key:
          if m.group('val') not in metadata[key]:
          # insert first, which means higher priority. Match_remove called from left
          # to right in path, so means last path component has highest priority
            metadata[key].insert(0, m.group('val'))
        #Cut away what we found but leave replace char as sign that something was there
        tmp = len(str) # Need to store offset if we change str length during iteration
        str = str[:m.start('val')+offset] + replace + str[m.end('val')+offset:]
        offset += len(str)-tmp
        i += 1
        if i == max:
          break
    return str    
#####################################################

def cmp_titles(x, y):
    xscore = len(x) - len(match_correctcase.sub("",x)) #Number of correct case pairs, e.g. Ab, not ab or AB
    yscore = len(y) - len(match_correctcase.sub("",y)) # More are better
    xscore += x.count(" ") # Number of spaces, more are better
    yscore += y.count(" ")
    return yscore-xscore
#     print "%s has %i score, %s has %i, returning score %s" % (
#       x,
#       len(x) - len(match_correctcase.sub("",x)),
#       y,
#       len(y) - len(match_correctcase.sub("",y)),
#       score
#     )
#   cx,cy = 0
#   score = 0
#   if len(x)>len(y):
#     score+=1
#   elif len(x)<len(y):
#     score-=1
#   cx = x.count(" ")
#   cy = y.count(" ")
#   if cx>cy:
#     score+=1
#   elif cx<cy:
#     score-=1
#   xcaps = len(x) - len(match_caps.sub("",x))
#   xsmall = len(x) - len(match_small.sub("",x))
#   ycaps = len(y) - len(match_caps.sub("",y))
#   ysmall = len(y) - len(match_small.sub("",y))
#   if xsmall==0 or ysmall==0:
#     if ysmall==0:
#       score+=1
#     else:
#       score-=1
#   else:
#     if (xcaps/xsmall) < (ycaps/ysmall):
    return score
#####################################################

def analyze_video_file(components, file):
  metadata = dict.fromkeys(format_keys.keys())
  for k in metadata.iterkeys():
    metadata[k] = []
  components = list(components)
  components.append(file) 
  for str in components:
    #if str==file: #only check extension on the file itself
    str = match_remove(match_extension, str, metadata, 'ext')
    
    str = match_remove(match_part, str, metadata, 'part')
    str = match_remove(match_part_alt, str, metadata, 'part')
    str = match_remove(match_video, str, metadata,'video_codec')
    str = match_remove(match_year, str, metadata, 'year')
    str = match_remove(match_sound, str, metadata, 'sound_codec')
    str = match_remove(match_rip, str, metadata, 'rip')    
    str = match_remove(match_resolution, str, metadata, 'resolution')
    str = match_remove(match_torrent, str, metadata, 'torrent')
    str = match_remove(match_title, str, metadata, 'title')
    str = match_remove(match_lang, str, metadata['lang'])
    
    newtitles = []
    for title in metadata['title']:
      #print "Before: '%s'" % title
      title = match_remove(match_clean1, title, replace=' ', max=10)
      #print "1: '%s'" % title
      title = match_remove(match_clean2, title, replace=' ', max=10)
      #print "2: '%s'" % title
      title = match_remove(match_clean3, title, replace=' - ', max=10)
      #print "3: '%s'" % title
      title = title.strip()
      #title = string.capwords(title)
      
      if len(title)>0 and title not in newtitles:
        newtitles.append(title)
    
    metadata['title'] = newtitles
    
    parts = match_split.split(str)
    for part in parts:
      part = match_remove(match_clean1, part, replace=' ', max=10)
      #print "1: '%s'" % part
      part = match_remove(match_clean2, part, replace=' ', max=10)
      #print "2: '%s'" % part
      part = match_remove(match_clean3, part, replace=' ', max=10)
      #print "3: '%s'" % part
      part = part.strip()
      if part=='-':
        print parts
      if len(part)>0  and part not in metadata['discarded']:
        metadata['discarded'].append(part)
  
  if len(metadata['title'])>1:
    metadata['title'].sort(cmp=cmp_titles)
#    sorted_titles = sorted(metadata['title'], cmp=cmp_titles)
    #print "Best title: %s" % sorted_titles[0]
  
  formatdata = dict()
  
  for k in chosen_format_keys:
    if k in metadata and len(metadata[k])>0:
      formatdata[k]=metadata[k][0] # Pick first item
    else:
      formatdata[k]='$$' #Placeholder so we can clean up later
  
  if 'filetype' in chosen_format_keys:
    formatdata['filetype'] = video_types[metadata['ext'][0]]
  if 'filename' in chosen_format_keys:
    formatdata['filename'] = file
  if 'mediaroot' in chosen_format_keys:
    #TODO incorrect mediaroot, should be only highest dir
    formatdata['mediaroot'] = os.path.join(components, file)   
  
  # Print only the metadata items that has a value
  #print "\n%s\n\t%s" % ('/'.join(components), dict([(k,v) for k,v in metadata.iteritems() if len(v)>0]))
  #print "Meta: %s" % dict([(k, metadata[k]) for k in metadata.iterkeys() if len(metadata[k])>0]) 

  if 'ext' in formatdata and metadata['ext'][0] == "VIDEO_TS":
    template = Template(args.format.replace(".$ext", os.path.sep+"$ext"))
  else:
    template = Template(args.format)
  
  #print template.substitute(formatdata) 
    
  newpath,newfile = (match_unfilled_format_keys.sub("",template.substitute(formatdata))).rsplit("/", 1)
  #print "newpath=%s, newfile=%s" % (newpath, newfile)
  return newpath, newfile
#####################################################

def has_media(files, path, orig_root):
  rarfiles = fnmatch.filter(files, "*.rar")
  extracted_files = False
  # If there are rar file, find out if they contain media
#   for rarfile in rarfiles:
#     queue_cmd(rarlist_cmd, os.path.join(path, rarfile))
#     results,retcode = pop_cmd()
# #    print results
#     c = 0
#     maxc = 50
#     for ext in match_fileext_in_results.findall(results):
#       c+=1
#       if c>maxc: # in case of very many files, break after 50
#         break
# #      print ext
#       if ext.lower() in video_types:
#         #Exract rar to same dir
#         queue_cmd(unrar_cmd, os.path.join(path, rarfile), path)
#         pop_cmd()
#         extracted_files = True
#         break
#   if extracted_files:
#     files = [f for f in os.listdir(root) if os.path.isfile(f)]
#     print "New files in %s: %s" (path, files)
  for file in files:
    fname, ext = os.path.splitext(file)
    ext = ext.strip('.').lower()
    if ext in video_types: #a file in dir has right extension
      # Move the whole directory tree from its root
      # make sure we can't overwrite anything
      dirname = os.path.basename(orig_root)
      while os.path.exists(os.path.join(args.media_dir, dirname)):
        dirname="copy_"+dirname
      move(orig_root, os.path.join(args.media_dir, dirname))
      return True
  return False
#####################################################

def fnmatch_multi(file, patterns):
  f = file.lower()
  for p in patterns:
    if fnmatch.fnmatch(f, p):
      return True
  return False
#####################################################
   
if args.import_dirs:    
  for import_dir in args.import_dirs:
    subdirs = [d for d in os.listdir(import_dir) if os.path.isdir(os.path.join(import_dir,d)) and 
      (d+".torrent" not in files_downloading) and (os.path.join(import_dir,d) not in args.noimport)]
    for filter in noimport_filters:
      subdirs = [d for d in subdirs if not fnmatch.fnmatch(d, filter)]
    
    for subdir in subdirs:
      for path,subsubdirs,files in os.walk(os.path.join(import_dir,subdir)):
        subsubdirs[:] = [ssd for ssd in subsubdirs if os.path.join(path, ssd) not in args.noimport]
        for filter in noimport_filters:
          subsubdirs[:]= [ssd for ssd in subsubdirs if not fnmatch.fnmatch(ssd, filter)]
          files = [f for f in files if not fnmatch.fnmatch(f, filter)]
        if has_media(files, path, os.path.join(import_dir,subdir)):
          #print "Found video in %s, breaking" % root
          break # Don't traverse deeper once we know this contained video
  flush_cmds()
  init_cmds()

recent_videos = []
no_subs_videos = []

dircount_cache = dict()

recent_limit = timedelta(weeks=4)
reverse_moves = dict()

for root, dirs, files in os.walk(args.media_dir):
  mediafiles = []
  subfiles = dict() # need to associate subs with their media files, so need to hash name before ext
  keepfiles = []
  deletefiles = []
  metafiles = []
  
  dirs[:] = [d for d in dirs if d not in args.exclude]

  if "VIDEO_TS" in dirs: #Special treatment of DVD images
    dirs.remove("VIDEO_TS")
    mediafiles.append("VIDEO_TS")
  
  dircount_cache[root]=len(dirs)
  
  need_subs = False
  for file in files:
    fname, ext = os.path.splitext(file)
    ext = ext.strip('.').lower()
    if fnmatch_multi(file, args.keepfiles):
      keepfiles.append(file)
    elif fnmatch_multi(file, args.deletefiles):
      deletefiles.append(file)
    elif ext in video_types: #a file in dir has right extension
      if "sample" in fname.lower() or "trailer" in fname.lower():
        metafiles.append(file)
      else:
        mediafiles.append(file)
        if ext in find_subtitles:
          need_subs = True
    elif ext in subfiles_ext:
      if fname in subfiles:
        subfiles[fname].append(ext)
      else:
        subfiles[fname] = [ext]
    else:
      metafiles.append(file)
  
  if len(mediafiles)>0: #We have found a media file root!
    print "\n%s\n%s" % (root, ''.ljust(len(root),'-')) # Root as title with equal length of dashes under
    
    # A note on time: because we may use mounted network volumes, created, modified and accessed
    # time may be incorrect depending on implementation. We want to know if
    # this movie was recently added to directory. If we check the timestamp of the directory,
    # that should reflect the last time we changed it, because the directory will be created
    # or changed when this script first encounters the movie.
    # That should be ok to use if the video was recently added to library or not
    dt_modified = datetime.fromtimestamp(os.path.getctime(root))
    dt_recent = datetime.now()-recent_limit
    #print "%s modified %s and limit is %s. Recent file? %s" % (root, dt_modified, dt_recent, (dt_modified > dt_recent))
    if dt_modified > dt_recent:
      recent_videos.append(root)
    
    ## DELETE FILES ###########
    # Add all files to delete, make full path of each file
    if deletefiles:
      queue_cmd(rm_cmd, *[os.path.join(root, file) for file in deletefiles])
    
    ## METAFILES ##############
    #  All files and subdirs below a video containing dir will by definition be included
    metafiles.extend(dirs) # treat subdirs as meta because we are in the media dir
    metapath = os.path.join(root, meta_dir_name,"")
    has_meta = meta_dir_name in metafiles
    if has_meta:
      metafiles.remove(meta_dir_name) # do not traverse into metadata, as we have created them before
    if len(metafiles)>0:
      move(root, metafiles, metapath)

    ## NEW SUBFILES ############
    # Only try to download subs if video files found can handle subs and there are no subs already
    # Do this before media files because it's probably better to search subtitles before renaming
    # to get the most original format
    if args.subs and need_subs and len(subfiles)==0:
      queue_cmd(periscope_cmd, *[os.path.join(root, f) for f in mediafiles])
      pop_cmd()      
      subfiles = dict() # need to start afresh
      for s in os.listdir(root):
        fname, ext = os.path.splitext(s)
        ext = ext.strip('.').lower()
        if ext in subfiles_ext:
          if fname in subfiles:
            subfiles[fname].append(ext)
          else:
            subfiles[fname] = [ext]
      
    ## MEDIA FILES ##########
    ## Finally handle the media files. Do this last because paths will change!
    #print "Mediaroot: %s" % root
    components = []
    (path, dir) = os.path.split(root)
    components.append(dir)
    while path != args.media_dir and dircount_cache[path]<3:
      (path, dir) = os.path.split(path)
      components.append(dir)
    moves = dict()

    for file in mediafiles:
      newpath, newfile = analyze_video_file(components, file)
      if newpath in moves:
        moves[newpath].append((file, newfile))
      else:
        moves[newpath] = [(file, newfile)]
    #print moves
    moved_meta_already = False
    files_left_in_root = False
    for newpath in moves.iterkeys():
      #At least one set of files will need to stay in this directory  
        # Means mv path/* cannot be used
      same_dir = (os.path.join(args.media_dir,newpath)==root)
      conc_moves = []
      
      for f,nf in moves[newpath]:
        newroot = os.path.join(args.media_dir,newpath)
        fname, ext = os.path.splitext(f)
        ext = ext.strip('.').lower()
        if f!=nf: # A file needs to be renamed
          move(root,f, newroot, nf)
          if fname in subfiles: # Move all associated subfiles along with this
            nf_name, nf_ext = os.path.splitext(nf)
            for e in subfiles[fname]:
              move(root,fname+'.'+e, newroot, nf_name+'.'+e)
            del subfiles[fname]
        elif not same_dir: # Files are same, but root is different
          conc_moves.append(f)
          if fname in subfiles:
            conc_moves.extend([fname+'.'+e for e in subfiles[fname]])
            del subfiles[fname]
      if not same_dir and not moved_meta_already:
      # We can only move this once per root dir (may be several destinations)
      # But we DONT move if the dest is same as root
        if len(subfiles)>0: #There are subfiles left to move to new dir
          for sub in subfiles:
            conc_moves.extend([sub+'.'+e for e in subfiles[sub]])
          print "WARNING, orphan subfiles> %s" % (conc_moves)          
        if len(metafiles)>0:
          if has_meta:
            conc_moves.append('meta_dir_name')
            metafiles.remove(meta_dir_name) 
        conc_moves.extend(keepfiles)
        moved_meta_already = True
      # No files should be left in orig dir, and we only have one destination
      if len(conc_moves)>0:
        if not same_dir and len(moves)==1:
          # If we are not moving in the same dir, and we have one destination total,
          # we can use wildcard to move all files left at once, set all=True
          move(root, conc_moves,newroot,all=True)
        else:
          # Move the rest individually if there are any
          move(root, conc_moves, newpath)       

    del dirs[:] # Don't continue deeper 
  
#print "Recent files: %s" % recent_videos
print "No subs: %s" % no_subs_videos
        
#flush_cmds()# run all queued commands 