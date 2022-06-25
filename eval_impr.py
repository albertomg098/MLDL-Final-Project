
"""
With this script you can evaluate checkpoints or test models from two popular
landmark retrieval github repos.
The first is https://github.com/naver/deep-image-retrieval from Naver labs, 
provides ResNet-50 and ResNet-101 trained with AP on Google Landmarks 18 clean.
$ python eval.py --off_the_shelf=naver --l2=none --backbone=resnet101conv5 --aggregation=gem --fc_output_dim=2048

The second is https://github.com/filipradenovic/cnnimageretrieval-pytorch from
Radenovic, provides ResNet-50 and ResNet-101 trained with a triplet loss
on Google Landmarks 18 and sfm120k.
$ python eval.py --off_the_shelf=radenovic_gldv1 --l2=after_pool --backbone=resnet101conv5 --aggregation=gem --fc_output_dim=2048
$ python eval.py --off_the_shelf=radenovic_sfm --l2=after_pool --backbone=resnet101conv5 --aggregation=gem --fc_output_dim=2048

Note that although the architectures are almost the same, Naver's
implementation does not use a l2 normalization before/after the GeM aggregation,
while Radenovic's uses it after (and we use it before, which shows better
results in VG)
"""

import os
import sys
import torch
import parser
import logging
import sklearn
from os.path import join
from datetime import datetime
from torch.utils.model_zoo import load_url
from google_drive_downloader import GoogleDriveDownloader as gdd

import test_ensam
import test_impr
import util
import commons
import datasets_ws
from model import network

OFF_THE_SHELF_RADENOVIC = {
    'resnet50conv5_sfm'    : 'http://cmp.felk.cvut.cz/cnnimageretrieval/data/networks/retrieval-SfM-120k/rSfM120k-tl-resnet50-gem-w-97bf910.pth',
    'resnet101conv5_sfm'   : 'http://cmp.felk.cvut.cz/cnnimageretrieval/data/networks/retrieval-SfM-120k/rSfM120k-tl-resnet101-gem-w-a155e54.pth',
    'resnet50conv5_gldv1'  : 'http://cmp.felk.cvut.cz/cnnimageretrieval/data/networks/gl18/gl18-tl-resnet50-gem-w-83fdc30.pth',
    'resnet101conv5_gldv1' : 'http://cmp.felk.cvut.cz/cnnimageretrieval/data/networks/gl18/gl18-tl-resnet101-gem-w-a4d43db.pth',
}

OFF_THE_SHELF_NAVER = {
    "resnet50conv5"  : "1oPtE_go9tnsiDLkWjN4NMpKjh-_md1G5",
    'resnet101conv5' : "1UWJGDuHtzaQdFhSMojoYVQjmCXhIwVvy"
}

######################################### SETUP #########################################
args = parser.parse_arguments()

start_time = datetime.now()
args.save_dir = join("test", args.save_dir, start_time.strftime('%Y-%m-%d_%H-%M-%S'))
commons.setup_logging(args.save_dir)
commons.make_deterministic(args.seed)
logging.info(f"Arguments: {args}")
logging.info(f"The outputs are being saved in {args.save_dir}")

start_time = datetime.now()
if args.comparison:
  args1 = parser.parse_arguments()
  args1.resume = args1.resume_compar
  args1.aggregation = 'gem'
  args1.save_dir = args1.save_alt_dir
  args1.save_dir = join("test", args1.save_dir, start_time.strftime('%Y-%m-%d_%H-%M-%S'))
  args1.pca_dim = None
  #commons.setup_logging(args1.save_dir)
  #commons.make_deterministic(args1.seed)
  #logging.info(f"Arguments: {args1}")
  #logging.info(f"The outputs are being saved in {args1.save_dir}")

######################################### MODEL #########################################
model1 = network.GeoLocalizationNet(args)
model1 = model1.to(args.device)
model1 = torch.nn.DataParallel(model1)

if args.comparison:
  model2 = network.GeoLocalizationNet(args1)
  model2 = model2.to(args1.device)
  model2 = torch.nn.DataParallel(model2)
 
 

if args.aggregation in ["netvlad", "crn"]:
    args.features_dim *= args.netvlad_clusters

if args.off_the_shelf.startswith("radenovic") or args.off_the_shelf.startswith("naver"):
    if args.off_the_shelf.startswith("radenovic"):
        pretrain_dataset_name = args.off_the_shelf.split("_")[1]  # sfm or gldv1 datasets
        url = OFF_THE_SHELF_RADENOVIC[f"{args.backbone}_{pretrain_dataset_name}"]
        state_dict = load_url(url, model_dir=join("data", "off_the_shelf_nets"))
    else:
        # This is a hacky workaround to maintain compatibility
        sys.modules['sklearn.decomposition.pca'] = sklearn.decomposition._pca
        zip_file_path = join("data", "off_the_shelf_nets", args.backbone + "_naver.zip")
        if not os.path.exists(zip_file_path):
            gdd.download_file_from_google_drive(file_id=OFF_THE_SHELF_NAVER[args.backbone],
                                                dest_path=zip_file_path, unzip=True)
        if args.backbone == "resnet50conv5":
            state_dict_filename = "Resnet50-AP-GeM.pt"
        elif args.backbone == "resnet101conv5":
            state_dict_filename = "Resnet-101-AP-GeM.pt"
        state_dict = torch.load(join("data", "off_the_shelf_nets", state_dict_filename))
    state_dict = state_dict["state_dict"]
    model_keys = model1.state_dict().keys()
    renamed_state_dict = {k: v for k, v in zip(model_keys, state_dict.values())}
    model1.load_state_dict(renamed_state_dict)
elif args.resume != None:
    state_dict = torch.load(args.resume)["model_state_dict"]
    model1.load_state_dict(state_dict)

if args.pca_dim == None:
    pca = None
else:
    full_features_dim = args.features_dim
    args.features_dim = args.pca_dim
    pca = util.compute_pca(args, model1, args.pca_dataset_folder, full_features_dim)

# MODEL 2 SETUP 
if args.comparison:
  if args1.aggregation in ["netvlad", "crn"]:
    args1.features_dim *= args.netvlad_clusters

  if args1.off_the_shelf.startswith("radenovic") or args1.off_the_shelf.startswith("naver"):
      if args1.off_the_shelf.startswith("radenovic"):
          pretrain_dataset_name_2 = args1.off_the_shelf.split("_")[1]  # sfm or gldv1 datasets
          url_2 = OFF_THE_SHELF_RADENOVIC[f"{args1.backbone}_{pretrain_dataset_name_2}"]
          state_dict_2 = load_url(url_2, model_dir=join("data", "off_the_shelf_nets"))
      else:
          # This is a hacky workaround to maintain compatibility
          sys.modules['sklearn.decomposition.pca'] = sklearn.decomposition._pca
          zip_file_path_2 = join("data", "off_the_shelf_nets", args1.backbone + "_naver.zip")
          if not os.path.exists(zip_file_path_2):
              gdd.download_file_from_google_drive(file_id=OFF_THE_SHELF_NAVER[args1.backbone],
                                                  dest_path=zip_file_path_2, unzip=True)
          if args1.backbone == "resnet50conv5":
              state_dict_filename_2 = "Resnet50-AP-GeM.pt"
          elif args1.backbone == "resnet101conv5":
              state_dict_filename_2 = "Resnet-101-AP-GeM.pt"
          state_dict_2 = torch.load(join("data", "off_the_shelf_nets", state_dict_filename_2))
      state_dict_2 = state_dict["state_dict"]
      model_keys_2 = model2.state_dict().keys()
      renamed_state_dict_2 = {k: v for k, v in zip(model_keys_2, state_dict_2.values())}
      model2.load_state_dict(renamed_state_dict_2)
  elif args1.resume != None:
      state_dict_2 = torch.load(args1.resume)["model_state_dict"]
      model2.load_state_dict(state_dict_2)

  if args1.pca_dim == None:
      pca_2 = None
  else:
      full_features_dim_2 = args1.features_dim
      args.features_dim_2 = args1.pca_dim
      pca_2 = util.compute_pca(args1, model2, args1.pca_dataset_folder, full_features_dim)

######################################### DATASETS #########################################
test_ds = datasets_ws.BaseDataset(args, args.datasets_folder, args.dataset_name, "test")
logging.info(f"Test set: {test_ds}")

######################################### TEST on TEST SET #########################################
if args.comparison:
  recalls, recalls_str = test_ensam.test(args, test_ds, model1, args.test_method, pca, args1, model2, args1.test_method, pca_2)
else:
  recalls, recalls_str = test_impr.test(args, test_ds, model1, args.test_method, pca)

logging.info(f"Recalls on {test_ds}: {recalls_str}")

logging.info(f"Finished in {str(datetime.now() - start_time)[:-7]}")

