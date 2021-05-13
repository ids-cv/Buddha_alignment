import os
import yaml
import shutil
import numpy as np
import matplotlib.pyplot as plt
from TDDFA_ONNX import TDDFA_ONNX
import matplotlib.patches as patches
from mpl_toolkits.mplot3d import Axes3D
from buddha_dataset import BuddhaDataset, Artifact, Image, Config, get_transform, ldk_on_im


class Pipeline:
    def __init__(self, config):
        cfg = yaml.load(open('configs/mb1_120x120.yml'), Loader=yaml.SafeLoader)
        os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
        os.environ['OMP_NUM_THREADS'] = '4'
        self.tddfa = TDDFA_ONNX(**cfg)
        self.save_intermediate = config.save_intermediate
        self.save_predict = config.save_predict
        self.save_eval = config.save_eval
        self.save_net_error = config.save_net_error
        self.path_products = config.path_products
        self.id_art = None
        self.id_img = None

    def train(self, input, label):
        net_error = self._get_network_error(input, label)

    def predict(self, input):
        self.id_art = input[0].split("_")[-1]
        list_x = []
        list_transform = []
        for image in input[1]:
            self.id_img = image[0].split(".")[0]
            x = self._get_landmarks(image[1])
            attention = self._get_attention(x)
            transform, x = self._normalize_position(x)
            x = self._preprocess_consensus(x, attention)
            list_x.append(x)
            list_transform.append(transform)
        list_x = np.asarray(list_x)
        x = self._get_consensus(list_x)
        self.id_art, self.id_img = None, None
        return list_transform, x

    def eval(self, input, label):
        revert_save_policy = False
        if self.save_intermediate:
            revert_save_policy = True
            self.save_intermediate = False
        list_transform, x = self.predict(input)
        self.id_art, gt, list_transform_gt = label
        transform, gt = self._normalize_position(gt)
        errors = [np.linalg.norm(pt_gt - pt_x) for pt_x, pt_gt in zip(x, gt)]
        if self.save_eval:
            self._save_eval(input, x, list_transform, gt,list_transform_gt, errors)
        if revert_save_policy:
            self.save_intermediate = True
        return errors

    def _get_landmarks(self, input):
        assert input.ndim == 3
        face_bbox = [0, 0, input.shape[0], input.shape[1]]
        param_lst, roi_box_lst = self.tddfa(input, [face_bbox])
        x = self.tddfa.recon_vers(param_lst, roi_box_lst, dense_flag=False)
        x = x[0].T
        if self.save_intermediate:
            self._save_get_landmarks(input, x)
        return x

    def _get_attention(self, input):
        assert input.ndim == 2
        _x = input - np.mean(input, axis=0)
        x = (_x / (2 * _x.max())) + .5
        triangles = [[0, 1, 36], [1, 36, 48], [1, 2, 48], [2, 3, 48], [3, 4, 48], [4, 48, 60], [4, 5, 60], [5, 59, 60],
                     [5, 6, 59], [6, 58, 59], [6, 7, 58], [7, 57, 58], [7, 8, 57], [8, 9, 57], [9, 56, 57], [9, 10, 56],
                     [10, 55, 56], [10, 11, 55], [11, 64, 55], [11, 12, 64], [12, 54, 64], [12, 13, 54], [13, 14, 54],
                     [14, 15, 54], [15, 45, 54], [15, 16, 45], [16, 26, 45], [26, 25, 45], [25, 44, 45], [25, 24, 44],
                     [24, 43, 44], [23, 24, 43], [23, 42, 43], [22, 23, 42], [21, 22, 23], [20, 21, 23], [20, 21, 39],
                     [20, 38, 39], [19, 20, 38], [19, 37, 38], [18, 19, 37], [18, 36, 37], [17, 18, 36], [0, 17, 36],
                     [36, 37, 41], [36, 40, 41], [40, 37, 38], [38, 39, 40], [42, 43, 47], [43, 44, 47], [44, 46, 47],
                     [44, 45, 46], [21, 27, 39], [27, 28, 39], [28, 29, 39], [29, 31, 39], [39, 40, 31], [40, 41, 31],
                     [31, 36, 41], [31, 36, 48], [21, 22, 27], [22, 27, 42], [27, 28, 42], [28, 29, 42], [29, 35, 42],
                     [35, 42, 47], [35, 46, 47], [35, 45, 46], [35, 45, 54], [29, 30, 31], [30, 31, 32], [30, 32, 33],
                     [30, 33, 34], [30, 34, 35], [29, 30, 35], [31, 48, 49], [31, 49, 50], [31, 32, 50], [32, 33, 50],
                     [33, 50, 51], [33, 51, 52], [33, 34, 52], [34, 35, 52], [35, 52, 53], [35, 53, 54], [48, 49, 60],
                     [49, 50, 61], [50, 51, 61], [51, 61, 62], [51, 62, 63], [51, 52, 63], [52, 53, 63], [53, 54, 64],
                     [49, 59, 60], [49, 59, 61], [49, 61, 67], [61, 62, 67], [62, 66, 67], [62, 65, 66], [62, 63, 65],
                     [55, 63, 65], [53, 55, 65], [53, 55, 64], [58, 59, 67], [58, 66, 67], [57, 58, 66], [56, 57, 66],
                     [56, 65, 66], [55, 56, 65]]
        triangles = [x[triangle] for triangle in np.asarray(triangles)]
        attention = self._are_visible(x, triangles)
        if self.save_intermediate:
            self._save_get_attention(x, attention)
        return attention

    def _normalize_position(self, input):
        assert input.ndim == 2
        # empirical estimation of the length and oriented shape of the standard alignment
        length = np.linalg.norm(input[0] - input[16])
        A = np.asarray([[0, 0, 0], [length, 0, 0], [length / 2, length / 6, length / 3]])
        B = np.asarray([input[0], input[16], input[33]])
        trans = get_transform(A, B)
        tmp = np.asarray(input.T.tolist() + [list([1] * 68)])
        x = (tmp.T @ trans).T[:3].T
        _mean = np.mean(x, axis=0)
        _x = x - _mean
        _max = _x.max()
        x = (_x / (2 * _max)) + .5

        if self.save_intermediate:
            self._save_normalize_position(x)
        return [trans, _mean, _max], x
    
    def _revert_normalize_position(self, input, trans, _mean, _max, gt=False):
        if gt:
            x = input
            x = np.asarray(x.T.tolist() + [list([1] * 68)])
            x = (x.T @ np.linalg.inv(trans))
            return ((x[:, :3]) * _max/2) + _mean
        else:
            x = ((input - .5) * 2 * _max) + _mean
            x = np.asarray(x.T.tolist() + [list([1] * 68)])
            return (x.T @ np.linalg.inv(trans))[:, :3]

    def _preprocess_consensus(self, input, attention):
        assert input.ndim == 2 and attention.ndim == 2
        x = input * attention
        tmp = np.concatenate((input, attention), axis=-1)
        if self.save_intermediate:
            self._save_preprocess_consensus(input, attention)
        return tmp

    def _get_consensus(self, input):
        assert input.ndim == 3
        x = np.zeros((68,3))
        for i in range(68):
            values = input[:, i, :-1]
            weights = input[:, i, -1]
            x[i] = np.average(values, axis=0, weights=weights)
        if self.save_predict:
            self._save_get_consensus(x)
        return x

    def _get_network_error(self, input, label):
        list_transform, x = self.predict(input)
        _, gt, list_transform_gt = label
        list_error = []
        for transform, transform_gt in zip(list_transform, list_transform_gt):
            proj_x = self._revert_normalize_position(x, transform[0], transform[1], transform[2])
            proj_gt = self._revert_normalize_position(gt, transform_gt[0], transform_gt[1], transform_gt[2], True)
            list_error.append(np.linalg.norm(proj_gt - proj_x, axis=1))
        if self.save_net_error:
            # self._save_pred_vs_gt_per_face(input, x, list_transform, gt, list_transform_gt)
            self._save_report(input, x, list_transform, gt, list_transform_gt)
        return list_error

    def _save_report(self, input, x, list_transform, gt, list_transform_gt):
        if not os.path.exists(os.path.join("/home/hlemarchant/report", input[0])):
            os.mkdir(os.path.join("/home/hlemarchant/report", input[0]))
        for data, transform, transform_gt in zip(input[1], list_transform, list_transform_gt):
            path = os.path.join("/home/hlemarchant/report", input[0], "image_" + data[0].split(".")[0])
            if not os.path.exists(path):
                os.mkdir(path)
            plt.imsave(os.path.join(path, "full.png"), data[2])
            fig, ax = plt.subplots()
            ax.imshow(data[1])
            tmp = self._revert_normalize_position(x, transform[0], transform[1], transform[2])
            cloud = tmp[:, :2]
            ax.scatter(cloud[:, 0], cloud[:, 1], c="r", s=15)
            plt.savefig(os.path.join(path, "pred"))
            fig, ax = plt.subplots()
            ax.imshow(data[1])
            tmp = self._revert_normalize_position(gt, transform_gt[0], transform_gt[1], transform_gt[2], True)
            cloud = tmp[:, :2]
            ax.scatter(cloud[:, 0], cloud[:, 1], c="r", s=15)
            plt.savefig(os.path.join(path, "gt"))

    def _save_get_landmarks(self, input, x):
        path = os.path.join(self.path_products, '0_SingleViewAlign')
        if not os.path.exists(path):
            os.mkdir(path)
        fig, ax = plt.subplots()
        ax.imshow(input)
        ax.scatter(x[:, 0], x[:, 1], c="red", s=5)
        plt.savefig(os.path.join(path, self.id_art + "_" + self.id_img))

    def _save_get_attention(self, x, attention):
        path = os.path.join(self.path_products, '1_Attention')
        if not os.path.exists(path):
            os.mkdir(path)
        fig = plt.figure()
        ax = Axes3D(fig)
        tmp = x.T
        for _x, _y, _z, _a in zip(tmp[0], tmp[1], tmp[2], attention.squeeze()):
            ax.scatter(_x, _y, _z, c='b', alpha=_a)
        plt.savefig(os.path.join(path, self.id_art + "_" + self.id_img))

    def _save_normalize_position(self, x):
        path = os.path.join(self.path_products, '2_NormalizedPos')
        if not os.path.exists(path):
            os.mkdir(path)
        fig = plt.figure()
        ax = Axes3D(fig)
        tmp = x.T
        ax.scatter(tmp[0], tmp[1], tmp[2], c='b')
        plt.savefig(os.path.join(path, self.id_art + "_" + self.id_img))

    def _save_preprocess_consensus(self, x, attention):
        path = os.path.join(self.path_products, '3_PreprocessConsensus')
        if not os.path.exists(path):
            os.mkdir(path)
        fig = plt.figure()
        ax = Axes3D(fig)
        tmp = x.T
        for _x, _y, _z, _a in zip(tmp[0], tmp[1], tmp[2], attention.squeeze()):
            ax.scatter(_x, _y, _z, c='b', alpha=_a)
        plt.savefig(os.path.join(path, self.id_art + "_" + self.id_img))

    def _save_get_consensus(self, x):
        path = os.path.join(self.path_products, 'Prediction')
        if not os.path.exists(path):
            os.mkdir(path)
        fig = plt.figure()
        ax = Axes3D(fig)
        tmp = x.T
        ax.scatter(tmp[0], tmp[1], tmp[2], c='b')
        plt.savefig(os.path.join(path, self.id_art))

    def _save_pred_vs_gt_per_face(self, input, x, list_transform, gt, list_transform_gt):
        path = os.path.join("self.path_products", 'Eval')
        if not os.path.exists(path):
            os.mkdir(path)
        size = int(np.sqrt(len(input[1]))) + 1
        nb_line = size - 1 if (len(input[1]) <= size * size - size) else size
        fig, axs = plt.subplots(nb_line, size, figsize=(25, 25), squeeze=False)
        id = 0
        for data, transform, transform_gt in zip(input[1], list_transform, list_transform_gt):
            img = data[1]
            axs[id // size, id % size].imshow(img)
            tmp = self._revert_normalize_position(x, transform[0], transform[1], transform[2])
            cloud = tmp[:, :2]
            axs[id // size, id % size].scatter(cloud[:, 0], cloud[:, 1], c="c", s=10)
            tmp = self._revert_normalize_position(gt, transform_gt[0], transform_gt[1], transform_gt[2], True)
            cloud = tmp[:, :2]
            axs[id // size, id % size].scatter(cloud[:, 0], cloud[:, 1], c="r", s=10)
            id = id + 1
        plt.savefig(os.path.join(path, self.id_art + "_2D_cyan_pred_VS_red_gt"))

    def _save_eval(self, input, x, list_transform, gt, list_transform_gt, errors):
        path = os.path.join(self.path_products, 'Eval')
        if not os.path.exists(path):
            os.mkdir(path)
        fig = plt.figure()
        ax = Axes3D(fig)
        tmp = x.T
        ax.scatter(tmp[0], tmp[1], tmp[2], c='c')
        tmp = gt.T
        ax.scatter(tmp[0], tmp[1], tmp[2], c='red')
        plt.savefig(os.path.join(path, self.id_art + "_3D_cyan_pred_VS_red_gt"))
        self._save_pred_vs_gt_per_face(input, x, list_transform, gt, list_transform_gt)
        fig, ax = plt.subplots()
        categories = ['all', 'jaw_line', 'mouth', 'nose', 'right_eye', 'right_eyebrow', 'left_eye', 'left_eyebrow']
        categorized_error = [np.sum(errors), np.sum(errors[:17]), np.sum(errors[48:]), np.sum(errors[27:36]),
                             np.sum(errors[36:42]), np.sum(errors[17:22]), np.sum(errors[42:48]), np.sum(errors[22:27])]
        ax.bar(categories, categorized_error)
        plt.savefig(os.path.join(path, self.id_art + "_error_per_category"))
        fig, ax = plt.subplots()
        data = [errors, errors[:17], errors[48:], errors[27:36], errors[36:42], errors[17:22], errors[42:48], errors[22:27]]
        ax.boxplot(data)
        plt.xticks([1, 2, 3, 4, 5, 6, 7, 8], ['all', 'jaw_line', 'mouth', 'nose', 'right_eye', 'right_eyebrow', 'left_eye', 'left_eyebrow'])
        plt.savefig(os.path.join(path, self.id_art + "_error_dispersion"))

    def _are_visible(self, list_x, triangles):
        result = []
        for x in list_x:
            visible = True
            for triangle in triangles:
                if x in triangle:
                    continue
                pt = self._intersect(x, triangle)
                if x[2] > pt[2]:
                    continue
                if self._is_within(pt, triangle):
                    visible = False
                    result.append([0.1])
                    break
            if visible:
                result.append([1])
        return np.asarray(result)

    def _intersect(self, x, triangle):
        planePoint = triangle[0]
        planeNormal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        rayPoint = x
        rayDirection = np.array([0, 0, 1])
        ndotu = planeNormal.dot(rayDirection)
        w = rayPoint - planePoint
        si = -planeNormal.dot(w) / ndotu
        return w + si * rayDirection + planePoint

    def _is_within(self, pt, triangle):
        area = 0.5 * np.linalg.norm(np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0]))
        sub_0 = 0.5 * np.linalg.norm(np.cross(triangle[1] - pt, triangle[2] - pt))
        sub_1 = 0.5 * np.linalg.norm(np.cross(triangle[2] - pt, triangle[0] - pt))
        sub_2 = 0.5 * np.linalg.norm(np.cross(triangle[0] - pt, triangle[1] - pt))
        x = np.abs(area - np.sum([sub_0, sub_1, sub_2]))
        return True if x < 1e-3 else False


if __name__ == '__main__':
    conf = Config('conf.json')
    ds = BuddhaDataset(conf)
    ds.load()
    ds.artifacts[0].print_gt()
    train_ds, test_ds, eval_ds = ds.get_datasets()
    train_data, train_label = train_ds
    test_data, test_label = test_ds
    eval_data, eval_label = eval_ds
    model = Pipeline(conf)
    if conf.train:
        print("INFO: Starting train routine...")
        for data, label in zip(train_data[:1], train_label[:1]):
            network_error = model._get_network_error(data, label)
    if conf.eval:
        print("INFO: Starting eval routine...")
        for data, label in zip(eval_data, eval_label):
            error = model.eval(data, label)
    if conf.test:
        print("INFO: Starting test routine...")
        for data, label in zip(test_data, test_label):
            error = model.eval(data, label)
