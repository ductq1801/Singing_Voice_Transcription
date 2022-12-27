import os
import re
import types
import logging
import uuid
import concurrent.futures
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import importlib
import csv
from ruamel import yaml
import librosa

import jsonschema
import pretty_midi
import numpy as np


def get_logger(name=None, level="warn"):
    """Get the logger for printing informations"""

    logger_name = str(uuid.uuid4())[:8] if name is None else name
    logger = logging.getLogger(logger_name)
    level = os.environ.get("LOG_LEVEL", level)

    msg_formats = {
        "debug": "%(asctime)s [%(levelname)s] %(message)s  [at %(filename)s:%(lineno)d]",
        "info": "%(asctime)s %(message)s  [at %(filename)s:%(lineno)d]",
        "warn": "%(asctime)s %(message)s",
        "warning": "%(asctime)s %(message)s",
        "error": "%(asctime)s [%(levelname)s] %(message)s  [at %(filename)s:%(lineno)d]",
        "critical": "%(asctime)s [%(levelname)s] %(message)s  [at %(filename)s:%(lineno)d]",
    }
    level_mapping = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }

    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt=msg_formats[level.lower()], datefmt=date_format)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    if len(logger.handlers) > 0:
        rm_idx = [idx for idx, handler in enumerate(logger.handlers) if isinstance(handler, logging.StreamHandler)]
        for idx in rm_idx:
            del logger.handlers[idx]
    logger.addHandler(handler)
    logger.setLevel(level_mapping[level.lower()])
    return logger

logger = get_logger("Utils")


class LazyLoader(types.ModuleType):
    """Lazily import a module"""

    def __init__(self, local_name, parent_module_globals, name, warning=None):
        self._local_name = local_name
        self._parent_module_globals = parent_module_globals
        self._warning = warning

        super().__init__(name)

    def _load(self):
        """Load the module and insert it into the parent's globals."""
        module = importlib.import_module(self.__name__)
        self._parent_module_globals[self._local_name] = module

        if self._warning:
            logger.warning(self._warning)
            # Make sure to only warn once.
            self._warning = None

        self.__dict__.update(module.__dict__)
        return module

    def __getattr__(self, item):
        module = self._load()
        return getattr(module, item)

    def __dir__(self):
        module = self._load()
        return dir(module)


# Lazy load the Spleeter pacakge for avoiding pulling large dependencies and boosting the import speed.
adapter = LazyLoader("adapter", globals(), "spleeter.audio.adapter")


def load_audio(audio_path, sampling_rate=44100, mono=True):
    audio, sr = librosa.load(audio_path, mono=mono, sr=sampling_rate)
    return audio, sr


def load_yaml(yaml_path):
    return yaml.round_trip_load(open(yaml_path, "r"), preserve_quotes=True)


def write_yaml(json_obj, output_path, dump=True):
    # json_obj should be yaml string already if dump is False
    out_str = yaml.round_trip_dump(json_obj, indent=4) if dump else json_obj
    open(output_path, "w").write(out_str)


def write_agg_f0_results(agg_f0, output_path):
    """Write out aggregated F0 information as a CSV file"""
    
    fieldnames = ["start_time", "end_time", "frequency", "pitch"]

    # Check the format is correct
    if any(list(row.keys()) != fieldnames for row in agg_f0):
        raise ValueError(f"Fields inconsistent! Expected: {fieldnames}")

    with open(output_path, "w") as out:
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(agg_f0)


def ensure_path_exists(path):
    if not os.path.exists(path):
        os.makedirs(path)


def camel_to_snake(string):
    """Convert a camel case to snake case"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", string).lower().replace("__", "_")


def snake_to_camel(string):
    """Convert a snake case to camel case"""
    return "".join(word.title() for word in string.split("_"))


def json_serializable(key_path="./", value_path="./"):
    """Class-level decorator for making a class json-serializable object"""

    def from_json(self, json_obj):
        if self.schema is not None:
            jsonschema.validate(instance=json_obj, schema=self.schema)
        if self.key_path.startswith("./"):
            self.key_path = self.key_path[2:]
        if self.value_path.startswith("./"):
            self.value_path = self.value_path[2:]

        # Get sub-object containing the target key-value pairs accroding to key_path.
        k_obj = json_obj
        if len(self.key_path) != 0:
            for key in self.key_path.split("/"):
                cmk = snake_to_camel(key)
                k_obj = k_obj[cmk]

        # Iterate through the target key-value pair, and assign the value to the object
        for key in self.__dict__:
            if key in self._ignore_list:
                continue

            camel_key = snake_to_camel(key)
            if hasattr(self.__dict__[key], "from_json"):
                # Another json-seriallizable object
                self.__dict__[key].from_json(k_obj[camel_key])
            elif camel_key not in k_obj:
                raise AttributeError(
                    f"Attribute '{camel_key}' is not defined in configuration file for class {type(self)}!"
                )
            else:
                # Parse the value according to value_path.
                # The path is relative to key_path.
                value = k_obj[camel_key]
                if len(self.value_path) != 0:
                    for v_key in self.value_path.split("/"):
                        cmv = snake_to_camel(v_key)
                        value = value[cmv]
                self.__dict__[key] = value
        return self

    def to_json(self):
        if self.key_path.startswith("./"):
            self.key_path = self.key_path[2:]
        if self.value_path.startswith("./"):
            self.value_path = self.value_path[2:]

        json_obj = {}
        ref = json_obj
        if len(self.key_path) != 0:
            for key in self.key_path.split("/"):
                camel_key = snake_to_camel(key)
                ref[camel_key] = {}
                ref = ref[camel_key]

        for key, value in self.__dict__.items():
            if key in self._ignore_list:
                continue

            camel_key = snake_to_camel(key)
            if hasattr(value, "to_json"):
                ref[camel_key] = value.to_json()
            elif len(self.value_path) != 0:
                ref[camel_key] = {}
                v_ref = ref[camel_key]
                v_keys = self.value_path.split("/")
                for v_key in v_keys[:-1]:
                    camel_v_key = snake_to_camel(v_key)
                    v_ref[camel_v_key] = {}
                    v_ref = v_ref[camel_v_key]
                camel_last_v_key = snake_to_camel(v_keys[-1])
                v_ref[camel_last_v_key] = value
            else:
                ref[camel_key] = value
        return json_obj

    def wrapper(tar_cls):
        setattr(tar_cls, "from_json", from_json)
        setattr(tar_cls, "to_json", to_json)
        setattr(tar_cls, "key_path", key_path)
        setattr(tar_cls, "value_path", value_path)
        setattr(tar_cls, "schema", None)
        setattr(tar_cls, "_ignore_list", ["_ignore_list", "key_path", "value_path", "schema"])
        return tar_cls

    return wrapper


def parallel_generator(func, input_list, max_workers=2, use_thread=False, chunk_size=None, timeout=600, **kwargs):
    if chunk_size is not None and max_workers > chunk_size:
        logger.warning(
            "Chunk size should larger than the maximum number of workers, or the parallel computation "
            "can do nothing helpful. Received max workers: %d, chunk size: %d",
            max_workers, chunk_size
        )
        max_workers = chunk_size

    executor = ThreadPoolExecutor(max_workers=max_workers) \
        if use_thread else ProcessPoolExecutor(max_workers=max_workers)

    chunks = 1
    slice_len = len(input_list)
    if chunk_size is not None:
        chunks = len(input_list) / chunk_size
        if int(chunks) < chunks:
            chunks = int(chunks) + 1
        slice_len = chunk_size

    for chunk_idx in range(int(chunks)):
        start_idx = chunk_idx * slice_len
        end_idx = (chunk_idx + 1) * slice_len
        future_to_input = {}
        for idx, _input in enumerate(input_list[start_idx:end_idx]):
            logger.debug("Parallel job submitted %s", func.__name__)
            future = executor.submit(func, _input, **kwargs)
            future_to_input[future] = idx + start_idx

        try:
            for future in concurrent.futures.as_completed(future_to_input, timeout=timeout):
                logger.debug("Yielded %s", func.__name__)
                yield future.result(), future_to_input[future]
        except KeyboardInterrupt as exp:
            for future in future_to_input:
                if future.cancel():
                    logger.info("Job cancelled")
                else:
                    logger.warning("Fail to cancel job: %s", future)
            executor.shutdown()
            raise exp
    executor.shutdown()


def resolve_dataset_type(dataset_path, keywords):
    low_path = os.path.basename(os.path.abspath(dataset_path)).lower()
    d_type = [val for key, val in keywords.items() if key in low_path]
    if len(d_type) == 0:
        return None

    assert len(set(d_type)) == 1
    return d_type[0]


def get_filename(path):
    abspath = os.path.abspath(path)
    return os.path.splitext(os.path.basename(abspath))[0]


def aggregate_f0_info(pred, t_unit):
    """Aggregation F0 contour to start time, end time, frequency, pitch"""

    results = []

    cur_idx = 0
    start_idx = 0
    last_hz = pred[0]
    eps = 1e-6
    pred = np.append(pred, 0)  # Append an additional zero to the end temporarily.
    while cur_idx < len(pred):
        cur_hz = pred[cur_idx]
        if abs(cur_hz - last_hz) < eps:
            # Skip to the next index with different frequency.
            last_hz = cur_hz
            cur_idx += 1
            continue

        if last_hz < eps:
            # Almost equals to zero. Ignored.
            last_hz = cur_hz
            start_idx = cur_idx
            cur_idx += 1
            continue

        results.append({
            "start_time": round(start_idx * t_unit, 6),
            "end_time": round(cur_idx * t_unit, 6),
            "frequency": last_hz,
            "pitch": pretty_midi.hz_to_note_number(last_hz)
        })

        start_idx = cur_idx
        cur_idx += 1
        last_hz = cur_hz

    pred = pred[:-1]  # Remove the additional ending zero.
    return results
