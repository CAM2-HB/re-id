from opt import args
import galleries
import detectors
import vectorgenerator
import loaders
from PIL import Image
from cropper import crop_image

import os
import numpy as np
import cv2
from sort import Sort
import bboxtrigger
from scipy.stats import mode
from tqdm import tqdm
from constants import *


"""
TODO:
    - write utility function for naming folders/files so don't overwrite old ones
    - compute time of different computations within program
        - try YOLO for detection (in previous tests, FasterRCNN has been slower than MGN)
        - get a working repo on ee220 computer
    - implement only one ID per frame constraint
"""

datapath = "../reid-data/msee2"


def init_args():
    parser = argparse.ArgumentParser(description="multi-camera re-id system")
    parser.add_argument("-d", "--detector", default=defaultkey, choices=detopt.keys())
    parser.add_argument("-r", "--distance", default=defaultkey, choices=distopt.keys())
    parser.add_argument("-l", "--loader", default=defaultkey, choices=loadopt.keys())
    parser.add_argument("-g", "--gallery", default=defaultkey, choices=galopt.keys())
    parser.add_argument("-v", "--vectgen", default=defaultkey, choices=vecopt.keys())
    parser.add_argument("-i", "--interval", default=2, type=int)
    parser.add_argument(
        "-video_path", required=True, help="Path to the video to run the pipeline on"
    )
    parser.add_argument(
        "-ref_image_path",
        required=True,
        help="Path to the reference image used for triggering",
    )
    return parser.parse_args()


def getvec2out(path, vecgen):
    if os.path.isfile(path):
        moiz = Image.open(path)
        return vecgen.getVect2(moiz)
    return None


def getMaxIndex(array, k=1):
    maxnums = array[0:k]
    maxinds = np.arange(0, k)
    minins = min(maxnums)
    mininin = maxnums.index(minins)
    for i, val in enumerate(array):
        if val > minins:
            maxnums[mininin] = val
            maxinds[mininin] = i
            minins = min(maxnums)
            mininin = maxnums.index(minins)
    if len(maxnums) == 1:
        return maxinds[0]
    else:
        return [x for _, x in sorted(zip(maxnums, maxinds), reverse=True)]


def loadPredefGal(path):
    if not os.path.exists(path):
        raise ValueError("path doesn't exist")
    imgfile = os.listdir(path)
    retval = list()
    for name in imgfile:
        na = ".".join(name.split(".")[:-1])
        img = getvec2out(os.path.join(path, name), vecgen)
        retval.append(img)
    return retval


###############################################################
def getVect(croppedimg):
    return vecgen.getVect2(croppedimg)


def main():
    args = init_args()
    detector = detectors.options[args.detector]()
    vecgen = vectorgenerator.options[args.vectgen]()
    dataloader = loaders.getLoader(args.video_path, args.loader, args.interval)

    ref_img = cv2.imread(args.ref_image_path)
    trig1 = bboxtrigger.BboxTrigger(
        "NE_Moiz",
        ref_img,
        DOOR_CLOSED_THRESHOLD,
        DOOR_OPEN_THRESHOLD,
        CHECK_OPEN_COORDS_ONE,
        TRIGGER_ROI_COORDS_ONE,
        detector,
    )
    # TODO : Thresholds might be different.
    trig2 = bboxtrigger.BboxTrigger(
        "NE_Moiz",
        ref_img,
        DOOR_CLOSED_THRESHOLD,
        DOOR_OPEN_THRESHOLD,
        CHECK_OPEN_COORDS_TWO,
        TRIGGER_ROI_COORDS_TWO,
        detector,
    )

    gallery = galleries.TriggerGallery(getVect)
    gallery.addTrigger(trig1)
    gallery.addTrigger(trig2)

    # create trackers for each video/camera
    trackers = {vidnames: Sort() for vidnames in dataloader.getVidNames()}
    outfiles = {
        vidnames: open(vidnames + "tmp.txt", "w")
        for vidnames in dataloader.getVidNames()
    }

    newfilenum = 0

    ###############################################################

    # iterate through frames of all cameras
    for findex, frames in tqdm(dataloader):

        # send frames from each camera to gallery to decide if references need to be captured based off triggering
        gallery.update(frames)

        # iterate through each camera
        for vidname, frame in frames.items():
            # get bounding boxes of all people
            boxes, scores = detector.getBboxes(frame)

            # send people bounding boxes to tracker
            # get three things: normal Sort output (tracking bounding boxes it wants to send), corresponding track objects, and objects of new tracks
            tracker = trackers[vidname]
            dets = np.column_stack((np.reshape(boxes, [-1, 4]), scores))
            tracks, trksoff, newtrks = tracker.update(dets)

            # find indexes of returned bounding boxes that meet ideal ratio
            trkbboxes = np.array(tracks)
            widths = trkbboxes[:, 2] - trkbboxes[:, 0]
            heights = trkbboxes[:, 3] - trkbboxes[:, 1]
            aspectratio = heights / widths
            readybools = np.isclose(aspectratio, 2, rtol=0.25)
            indexes = np.arange(len(tracks))[readybools]

            # iterate through returned bounding boxes
            for ind, trk in enumerate(tracks):
                box = ((int(trk[0]), int(trk[1])), (int(trk[2]), int(trk[3])))

                # if bounding box meets ideal ratio, save image of person as reference
                if ind in indexes:
                    cropimg = crop_image(frame, box)
                    if cropimg.size > 5:
                        newname = "tmpfiles/%07d.jpg" % newfilenum
                        newfilenum = newfilenum + 1
                        cv2.imwrite(newname, cropimg)
                        trksoff[ind].save_img(newname)

                # write bounding box, frame number, and trackid to file
                outfiles[vidname].write(
                    "%d,%d,%.2f,%.2f,%.2f,%.2f\n"
                    % (findex, trk[4], box[0][0], box[0][1], box[1][0], box[1][1])
                )

            # iterate through new tracks and add their current bounding box to list of track references
            for trk in newtrks:
                d = trk.get_state()[0]
                box = ((int(d[0]), int(d[1])), (int(d[2]), int(d[3])))
                cropimg = crop_image(frame, box)
                if cropimg.size > 5:
                    """
                    newname = "tmpfiles/%07d.jpg" % newfilenum
                    newfilenum = newfilenum + 1
                    cv2.imwrite(newname, cropimg)
                    trk.save_img(newname)

    # save images from gallery captured throughout video
    for i, img in enumerate(gallery.people):
        cv2.imwrite("tmpgal/%03d.jpg" % i, img)

    # load up the gallery (an artifact of debugging)
    mangall = loadPredefGal("tmpgal/")

    # write filename for all reference images for each track (artifact of debugging)
    for key in outfiles:
        outfiles[key].close()
        outfiles[key] = np.loadtxt(key + "tmp.txt", delimiter=",")

        traks = trackers[key]
        tracks = traks.trackers + traks.rejects
        file = open(key + "tmp2.txt", "w")
        for trk in tracks:
            file.write(str(trk.id) + " " + " ".join(trk.imgfiles) + "\n")
        file.close()

    # iterate through trackers for each camera
    for vidname, sorto in trackers.items():
        tracks = sorto.trackers + sorto.rejects
        convertdict = dict()

        # iterate through tracks within each tracker
        for trk in tqdm(tracks):
            reidimgs = trk.imgfiles

            # iterate through every reference image for this track
            for img in reidimgs:
                # get feature vector of image
                uniqvect = getvec2out(img, vecgen)

                if uniqvect is not None:
                    # find out what is the most similar gallery image
                    dists = [
                        np.average(np.dot(uniqvect, np.transpose(out2)))
                        for out2 in mangall
                    ]
                    index = getMaxIndex(dists, k=1)
                    trk.reid.append(index)

            # creating a dictionary mapping the trackIDs to the Re-IDs based on most frequent Re-ID of a track
            if len(trk.reid) > 0:
                convertdict[trk.id] = mode(trk.reid)[0][0]

        # utility function to do fancy numpy thing
        def convert(val):
            if val in convertdict:
                return convertdict[val]
            else:
                return val

        # the fancy numpy thing
        convertnp = np.vectorize(convert)

        # use numpy thing to change trackIDs to Re-IDs in output file
        outfiles[vidname][:, 1] = convertnp(outfiles[vidname][:, 1])

        # save
        np.savetxt(vidname + ".txt", outfiles[vidname])


if __name__ == "__main__":
    sys.exit(main())
