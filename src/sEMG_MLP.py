import numpy as np
import scipy.io as sio
import wandb
from tsai.all import *
from fastai.callback.wandb import *
from fastai.layers import *
from sklearn.metrics import accuracy_score
from mlconfound.stats import partial_confound_test

def LoadTrainTestFeatures(FEAT, LABEL, SUBJECT_SKINFOLD, sub_test):
  # Load testing samples
  X_Test     = FEAT[sub_test,0]
  Y_Test     = LABEL[sub_test,0].flatten()
  print(f'# of Testing Samples {len(Y_Test)}')

  # Load training samples
  X_Train = np.zeros((0,48))
  Y_Train = np.zeros(0)    
  C_Train = np.zeros(0)
  for sub_train in range(40):
    if sub_train != sub_test:
      x_s = FEAT[sub_train,0]
      y_s = LABEL[sub_train,0].flatten()
      c_s = np.mean(np.mean(SUBJECT_SKINFOLD[sub_train,:]), axis=1)
      X_Train = np.concatenate((X_Train, x_s), axis=0)
      Y_Train = np.concatenate((Y_Train, y_s), axis=0)
      C_Train = np.concatenate((C_Train, c_s), axis=0)

  print('# of Healthy Samples: %d'%(np.sum(Y_Train == -1)))
  print('# of Fatigued Samples: %d'%(np.sum(Y_Train == 1)))   
  
  return X_Train, Y_Train, C_Train, X_Test, Y_Test

# mainly just for the sake of not keeping the copy of DATA_ALL
def load_datafile(file):
  DATA_ALL = sio.loadmat(file)
  FEAT_N           = DATA_ALL['FEAT_N']            # Normalized features
  LABEL            = DATA_ALL['LABEL']             # Labels
  VFI_1            = DATA_ALL['SUBJECT_VFI']       # VFI-1 Score
  SUBJECT_ID       = DATA_ALL['SUBJECT_ID']        # Sujbect ID
  SUBJECT_SKINFOLD = DATA_ALL['SUBJECT_SKINFOLD']  # Subject Skinfold Thickness
  return FEAT_N, LABEL, SUBJECT_SKINFOLD, VFI_1, SUBJECT_ID

# environment variable for the experiment
WANDB = os.getenv("WANDB", False)
GROUP = os.getenv("GROUP", "MLP-sEMG")

if __name__ == "__main__":
  # X - FEAT_N
  # Y - LABEL
  # C - SUBJECT_SKINFOLD
  FEAT_N, LABEL, SUBJECT_SKINFOLD, VFI_1, SUBJECT_ID = load_datafile("data/subjects_40_v6")

  # NOTE
  # For the neural networks implementation, a high-level API was used in order to minimize implementation
  # more reference can be found in https://timeseriesai.github.io/tsai/
  train_acc = np.zeros(40)
  test_acc  = np.zeros(40)
  p_value   = np.zeros(40)
  for sub_test in range(40):
    sub_txt = "R%03d"%(int(SUBJECT_ID[sub_test][0][0]))
    sub_group = 'Fatigued' if int(VFI_1[sub_test][0][0][0]) > 10 else 'Healthy'
    print(f"Test Subject {sub_txt}:")
    print(f"VFI-1: {VFI_1[sub_test][0][0][0]}")

    cbs = None
    if WANDB:
      run = wandb.init(project="Confounding-Mitigation-In-Deep-Learning",
                       group=GROUP,
                       name=sub_txt,
                       tags=[sub_group],
                       reinit=True)
      cbs = WandbCallback(log_preds=False)

    #  Load training and testing features
    print("Loading training and testing set")
    X_Train, Y_Train, C_Train, X_Test, Y_Test = LoadTrainTestFeatures(FEAT_N, LABEL, SUBJECT_SKINFOLD, sub_test)

    # Setting "stratify" to True ensures that the relative class frequencies are approximately preserved in each train and validation fold.
    splits = get_splits(Y_Train, valid_size=.1, stratify=True, random_state=123, shuffle=True, show_plot=False)
    tfms   = [None, [Categorize()]]
    dsets       = TSDatasets(X_Train, Y_Train, tfms=tfms, splits=splits)
    dsets_train = TSDatasets(X_Train, Y_Train, tfms=tfms) # keep an unsplit copy for computing the p-value
    dsets_test  = TSDatasets(X_Test,  Y_Test,  tfms=tfms)
    dls       = TSDataLoaders.from_dsets(dsets.train, dsets.valid, shuffle_train=True, bs=32, num_workers=0)
    dls_train = dls.new(dsets_train)
    dls_test  = dls.new(dsets_test)

    # This model is pre-defined in https://timeseriesai.github.io/tsai/models.mlp.html
    model = MLP(c_in=dls.vars, c_out=dls.c, seq_len=48, layers=[50, 50, 50], use_bn=True)
    print(model)

    # Training loop is abstracted by the fastai API
    learn = Learner(dls, model, loss_func=CrossEntropyLossFlat(), metrics=accuracy, cbs=cbs)
    learn.lr_find()
    learn.fit_one_cycle(50, lr_max=1e-3)

    train_preds, train_targets = learn.get_preds(dl=dls_train)
    train_acc[sub_test] = accuracy_score(train_targets, train_preds.argmax(dim=1))
    print(f"Training acc: {train_acc[sub_test]}")

    ret = partial_confound_test(train_targets.numpy(), train_preds.argmax(dim=1).numpy(), C_Train,
                                cat_y=True, cat_yhat=True, cat_c=False,
                                cond_dist_method='gam',
                                progress=True)
    p_value[sub_test] = ret.p
    print(f"P Value      : {p_value[sub_test]}")

    test_preds, test_targets = learn.get_preds(dl=dls_test)
    test_acc[sub_test] = accuracy_score(test_targets, test_preds.argmax(dim=1))
    print(f"Testing acc : {test_acc[sub_test]}")

    if WANDB:
      wandb.log({"subject_info/vfi_1" : int(VFI_1[sub_test][0][0]),
                 "metrics/train_acc" : train_acc[sub_test],
                 "metrics/test_acc"  : test_acc[sub_test],
                 "metrics/p_value"   : p_value[sub_test]})