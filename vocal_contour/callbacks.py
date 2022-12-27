import os
import abc
import numpy as np

from utils import write_yaml, get_logger, ensure_path_exists

logger = get_logger("Callbacks")


class Callback(metaclass=abc.ABCMeta):
    """Base class of all callback classes"""

    def __init__(self, monitor=None):
        if monitor is not None:
            self.monitor = monitor
            if "acc" in monitor:
                self.monitor_op = np.greater
            else:
                self.monitor_op = np.less

    def on_train_begin(self, history=None):
        pass

    def on_train_end(self, history=None):
        pass

    def on_epoch_begin(self, epoch, history=None):
        pass

    def on_epoch_end(self, epoch, history=None):
        pass

    def on_train_batch_begin(self, history=None):
        pass

    def on_train_batch_end(self, history=None):
        pass

    def on_test_batch_begin(self, history=None):
        pass

    def on_test_batch_end(self, history=None):
        pass

    def _set_model(self, model):
        self.model = model

    def _get_monitor_value(self, history, callback_name="Callback"):
        history = history or {"train": [], "validate": []}

        if self.monitor.startswith("val"):
            hist = history["validate"]
        else:
            hist = history["train"]

        if len(hist) > 0:
            current = hist[-1]

        metric = self.monitor.split("_")[-1]
        if metric == "acc":
            metric = "accuracy"
        score = current.get(metric)
        if score is None:
            logger.warning(
                "%s conditioned on metric %s "
                "which is not available. Available metrics are %s",
                callback_name, self.monitor, list(current.keys())
            )
        return score


class EarlyStopping(Callback):
    """Early stop the training after no improvement on the monitor for a certain period"""

    def __init__(self, patience=5, monitor="val_acc"):
        super().__init__(monitor=monitor)
        self.patience = patience
        self.stopped_epoch = 0

    def on_train_begin(self, history=None):
        self.wait = 0
        self.best = np.Inf if self.monitor_op == np.less else -np.Inf

    def on_epoch_end(self, epoch, history=None):
        assert hasattr(self, "model")
        score = self._get_monitor_value(history, callback_name="Early stopping")
        if score is None:
            return

        if self.monitor_op(score, self.best):
            self.best = score
            self.wait = 0
        else:
            self.wait += 1

        if self.wait >= self.patience:
            self.model.stop_training = True
            self.stopped_epoch = epoch

    def on_train_end(self, history=None):
        if self.stopped_epoch > 0:
            print("Early stopped training")


class ModelCheckpoint(Callback):
    """Saving the model during training, override the previous checkpoint"""

    def __init__(self, filepath, monitor='val_acc', save_best_only=False, save_weights_only=False):
        super().__init__(monitor=monitor)
        self.filepath = filepath
        self.save_best_only = save_best_only
        self.save_weights_only = save_weights_only

    def on_train_begin(self, history=None):
        self.best = np.Inf if self.monitor_op == np.less else -np.Inf

    def on_epoch_end(self, epoch, history=None):
        if self.save_best_only:
            score = self._get_monitor_value(history, callback_name="Model checkpoint")
            if score is None:
                return

            if self.monitor_op(score, self.best):
                self.best = score
                self._save_model()
        else:
            self._save_model()

    def _ensure_path_exists(self):
        if hasattr(self, "_path_checked") and self._path_checked:  # pylint: disable=E0203
            return
        ensure_path_exists(self.filepath)
        self._path_checked = True

    def _save_model(self):
        self._ensure_path_exists()
        if not self.save_weights_only:
            write_yaml(self.model.to_yaml(), os.path.join(self.filepath, "arch.yaml"), dump=False)
        self.model.save_weights(os.path.join(self.filepath, "weights.h5"))