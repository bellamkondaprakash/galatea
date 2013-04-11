from collections import OrderedDict
import theano.tensor as T
from pylearn2.costs.cost import Cost
from theano.printing import Print
from pylearn2.expr.nnet import softmax_ratio
from pylearn2.models.mlp import MLP
from pylearn2.utils import block_gradient
from pylearn2.utils import safe_izip
from pylearn2.utils import sharedX
from theano.sandbox.rng_mrg import MRG_RandomStreams
import warnings


class BoostTry1(Cost):
    """
    This isn't thought through all that carefully, probably not correct at all
    """

    supervised = True

    def __call__(self, model, X, Y, **kwargs):

        Y = Y * 2 - 1

        # Get the approximate ensemble predictions
        Y_hat = model.fprop(X, apply_dropout=False)
        # Pull out the argument to the sigmoid
        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op

        if not hasattr(op, 'scalar_op'):
            raise ValueError("Expected Y_hat to be generated by an Elemwise op, got "+str(op)+" of type "+str(type(op)))
        assert isinstance(op.scalar_op, T.nnet.sigm.ScalarSigmoid)
        F ,= owner.inputs

        weights = - Y * T.nnet.softmax(-(Y * F).T).T

        weights = block_gradient(weights)


        # Get the individual model predictions
        Y_hat = model.fprop(X, apply_dropout=True)
        # Pull out the argument to the sigmoid
        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op

        if not hasattr(op, 'scalar_op'):
            raise ValueError("Expected Y_hat to be generated by an Elemwise op, got "+str(op)+" of type "+str(type(op)))
        assert isinstance(op.scalar_op, T.nnet.sigm.ScalarSigmoid)
        f ,= owner.inputs

        cost = (weights * T.exp(-Y * f)).mean()

        assert cost.ndim == 0

        return cost

class BoostTry2(Cost):
    """
    This isn't thought through all that carefully, probably not correct at all
    """

    supervised = True

    def __call__(self, model, X, Y, **kwargs):

        Y_hat = model.fprop(X, apply_dropout=False)
        prob = Y_hat * Y + (1-Y_hat) * (1-Y)

        weight = 1./(.1 + prob)

        weight = block_gradient(weight)

        Y_hat = model.fprop(X, apply_dropout=True)
        # Pull out the argument to the sigmoid
        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op

        if not hasattr(op, 'scalar_op'):
            raise ValueError("Expected Y_hat to be generated by an Elemwise op, got "+str(op)+" of type "+str(type(op)))
        assert isinstance(op.scalar_op, T.nnet.sigm.ScalarSigmoid)
        Z ,= owner.inputs

        term_1 = Y * T.nnet.softplus(-Z)
        term_2 = (1 - Y) * T.nnet.softplus(Z)

        total = term_1 + term_2

        total = weight * total

        ave = total.mean()

        return ave

#Try3 had a bug

class BoostTry4(Cost):

    supervised = True

    def __init__(self, k = 1, alpha = 1, beta =1):
        self.k = k
        self.alpha = alpha
        self.beta = beta

    def get_weight(self, model, X, Y):

        ensemble_Y = model.fprop(X, apply_dropout=False)
        prob_of = (ensemble_Y * Y).sum(axis=1)

        weight = 1./ (self.k + self.alpha * (prob_of - self.beta * 1./T.cast(Y.shape[1], 'float32')))
        weight = weight / weight.sum()
        weight = block_gradient(weight)
        return weight

    def get_monitoring_channels(self, model, X, Y, ** kwargs):

        weight = self.get_weight(model, X, Y)

        return { 'weight_min': weight.min(),
                'weight_max': weight.max(),
                'weight_mean' : weight.mean() }


    def __call__(self, model, X, Y, **kwargs):

        weight = self.get_weight(model, X, Y)

        Y_hat = model.fprop(X, apply_dropout=True)

        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op
        if isinstance(op, Print):
            assert len(owner.inputs) == 1
            Y_hat, = owner.inputs
            owner = Y_hat.owner
            op = owner.op
        assert isinstance(op, T.nnet.Softmax)
        z ,= owner.inputs
        assert z.ndim == 2

        z = z - z.max(axis=1).dimshuffle(0, 'x')
        log_prob = z - T.log(T.exp(z).sum(axis=1).dimshuffle(0, 'x'))
        # we use sum and not mean because this is really one variable per row
        log_prob_of = (Y * log_prob).sum(axis=1)
        assert log_prob_of.ndim == 1

        weighted_log_prob_of = T.dot(weight, log_prob_of)


        return - weighted_log_prob_of


class EnsembleLikelihoodTrainOne(Cost):

    supervised = True

    def __call__(self, model, X, Y, **kwargs):

        Y_hat_e = model.fprop(X)
        Y_hat = model.fprop(X, apply_dropout=True)

        softmax_r = softmax_ratio(Y_hat_e, Y_hat)

        softmax_r = block_gradient(softmax_r)

        neg_terms = softmax_r * Y_hat

        neg = - neg_terms.sum(axis=1).mean(axis=0)

        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op
        if isinstance(op, Print):
            assert len(owner.inputs) == 1
            Y_hat, = owner.inputs
            owner = Y_hat.owner
            op = owner.op
        assert isinstance(op, T.nnet.Softmax)
        z ,= owner.inputs
        assert z.ndim == 2

        z = z - z.max(axis=1).dimshuffle(0, 'x')
        log_prob = z - T.log(T.exp(z).sum(axis=1).dimshuffle(0, 'x'))
        # we use sum and not mean because this is really one variable per row
        log_prob_of = (Y * log_prob).sum(axis=1)
        assert log_prob_of.ndim == 1
        log_prob_of = log_prob_of.mean()

        return -(log_prob_of + neg)

class PoE_SameMask(Cost):

    supervised = True

    def __init__(self, alpha = 1):
        self.alpha = alpha

    def __call__(self, model, X, Y, **kwargs):

        Y_hat_e = model.fprop(X)
        Y_hat = model.fprop(X, apply_dropout=True)

        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op
        if isinstance(op, Print):
            assert len(owner.inputs) == 1
            Y_hat, = owner.inputs
            owner = Y_hat.owner
            op = owner.op
        assert isinstance(op, T.nnet.Softmax)
        z ,= owner.inputs
        assert z.ndim == 2

        z_weight = Y_hat - Y_hat_e
        z_weight = block_gradient(z_weight)
        neg = z_weight * z
        neg = neg.sum(axis=1).mean()

        z = z - z.max(axis=1).dimshuffle(0, 'x')
        log_prob = z - T.log(T.exp(z).sum(axis=1).dimshuffle(0, 'x'))
        # we use sum and not mean because this is really one variable per row
        log_prob_of = (Y * log_prob).sum(axis=1)
        assert log_prob_of.ndim == 1
        log_prob_of = log_prob_of.mean()

        return -(log_prob_of + self.alpha * neg)

class DropoutBoosting(Cost):
    """
    Like PoE_SameMask but with control over dropout probabilities and scaling
    """

    supervised = True

    def __init__(self, default_input_include_prob=.5, input_include_probs=None,
            default_input_scale=2., input_scales=None, alpha = 1.):
        """
        During training, each input to each layer is randomly included or excluded
        for each example. The probability of inclusion is independent for each input
        and each example. Each layer uses "default_input_include_prob" unless that
        layer's name appears as a key in input_include_probs, in which case the input
        inclusion probability is given by the corresponding value.

        Each feature is also multiplied by a scale factor. The scale factor for each
        layer's input scale is determined by the same scheme as the input probabilities.
        """

        if input_include_probs is None:
            input_include_probs = {}

        if input_scales is None:
            input_scales = {}

        self.__dict__.update(locals())
        del self.self

    def __call__(self, model, X, Y, ** kwargs):
        Y_hat = model.dropout_fprop(X, default_input_include_prob=self.default_input_include_prob,
                input_include_probs=self.input_include_probs, default_input_scale=self.default_input_scale,
                input_scales=self.input_scales
                )
        Y_hat_e = model.fprop(X)

        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op
        if isinstance(op, Print):
            assert len(owner.inputs) == 1
            Y_hat, = owner.inputs
            owner = Y_hat.owner
            op = owner.op
        assert isinstance(op, T.nnet.Softmax)
        z ,= owner.inputs
        assert z.ndim == 2

        z_weight = Y_hat - Y_hat_e
        z_weight = block_gradient(z_weight)
        neg = z_weight * z
        neg = neg.sum(axis=1).mean()

        z = z - z.max(axis=1).dimshuffle(0, 'x')
        log_prob = z - T.log(T.exp(z).sum(axis=1).dimshuffle(0, 'x'))
        # we use sum and not mean because this is really one variable per row
        log_prob_of = (Y * log_prob).sum(axis=1)
        assert log_prob_of.ndim == 1
        log_prob_of = log_prob_of.mean()

        return -(log_prob_of + self.alpha * neg)

class PerLayerRescaler(MLP):

    def __init__(self, mlp, max_scale = 10.):
        self.__dict__.update(locals())
        del self.self

        self._params = []
        for layer in mlp.layers:
            self._params.append(sharedX(1.))
        self.batch_size = mlp.batch_size
        self.force_batch_size = mlp.force_batch_size

    def get_input_space(self):
        return self.mlp.get_input_space()

    def get_params(self):
        return list(self._params)

    def censor_updates(self, updates):
        for key in updates:
            updates[key] = T.clip(updates[key], 0, self.max_scale)

    def fprop(self, state_below):

        for layer, scale in safe_izip(self.mlp.layers, self._params):
            state_below = layer.fprop(state_below * scale)

        return state_below

    def get_monitoring_channels(self, X=None, Y=None):
        """
        Note: X and Y may both be None, in the case when this is
              a layer of a bigger MLP.
        """

        state = X
        rval = OrderedDict()

        for layer, scale in safe_izip(self.mlp.layers, self._params):
            state = state * scale
            ch = layer.get_monitoring_channels()
            for key in ch:
                rval[layer.layer_name+'_'+key] = ch[key]
            state = layer.fprop(state)
            args = [state]
            if layer is self.mlp.layers[-1]:
                args.append(Y)
            ch = layer.get_monitoring_channels_from_state(*args)
            for key in ch:
                rval[layer.layer_name+'_'+key]  = ch[key]

        for i in xrange(len(self._params)):
            rval['scale_input_to_' + self.mlp.layers[i].layer_name] = self._params[i]

        return rval

    def get_output_space(self):
        return self.mlp.layers[-1].get_output_space()

    def get_weights(self):
        return self.mlp.get_weights()

    def get_weights_format(self):
        return self.mlp.get_weights_format()

    def get_weights_topo(self):
        return self.mlp.get_weights_topo()

    def cost(self, Y, Y_hat):
        return self.mlp.layers[-1].cost(Y, Y_hat)

    def get_lr_scalers(self):
        return {}


class PerUnitRescaler(MLP):

    def __init__(self, mlp, max_scale = 10.):
        self.__dict__.update(locals())
        del self.self

        self._params = []
        for layer in mlp.layers:
            self._params.append(sharedX(layer.get_input_space().get_origin() + 1.))
        self.batch_size = mlp.batch_size
        self.force_batch_size = mlp.force_batch_size

    def get_input_space(self):
        return self.mlp.get_input_space()

    def get_params(self):
        return list(self._params)

    def censor_updates(self, updates):
        for key in updates:
            updates[key] = T.clip(updates[key], 0, self.max_scale)

    def fprop(self, state_below):

        for layer, scale in safe_izip(self.mlp.layers, self._params):
            state_below = layer.fprop(self.scale(state_below, layer, scale))

        return state_below

    def scale(self, state, layer, scale):

        axes = range(state.ndim)
        if state.ndim == 2:
            axes = ('x', 0)
        else:
            assert tuple(layer.get_input_space().axes) == tuple(['c', 0, 1, 'b'])
            axes = (0, 1, 2, 'x')
        scaler = scale.dimshuffle(*axes)

        return state * scaler

    def get_monitoring_channels(self, X=None, Y=None):
        """
        Note: X and Y may both be None, in the case when this is
              a layer of a bigger MLP.
        """

        state = X
        rval = OrderedDict()

        for layer, scale in safe_izip(self.mlp.layers, self._params):
            state = self.scale(state, layer, scale)
            ch = layer.get_monitoring_channels()
            for key in ch:
                rval[layer.layer_name+'_'+key] = ch[key]
            state = layer.fprop(state)
            args = [state]
            if layer is self.mlp.layers[-1]:
                args.append(Y)
            ch = layer.get_monitoring_channels_from_state(*args)
            for key in ch:
                rval[layer.layer_name+'_'+key]  = ch[key]

        for i in xrange(len(self._params)):
            rval['scale_input_to_' + self.mlp.layers[i].layer_name + '_min'] = self._params[i].min()
            rval['scale_input_to_' + self.mlp.layers[i].layer_name + '_min'] = self._params[i].mean()
            rval['scale_input_to_' + self.mlp.layers[i].layer_name + '_min'] = self._params[i].max()

        return rval

    def get_output_space(self):
        return self.mlp.layers[-1].get_output_space()

    def get_weights(self):
        return self.mlp.get_weights()

    def get_weights_format(self):
        return self.mlp.get_weights_format()

    def get_weights_topo(self):
        return self.mlp.get_weights_topo()

    def cost(self, Y, Y_hat):
        return self.mlp.layers[-1].cost(Y, Y_hat)

    def get_lr_scalers(self):
        return {}



class LoneRangerDropoutBoosting(Cost):
    """
    Like PoE_SameMask but with control over dropout probabilities and scaling
    """

    supervised = True

    def __init__(self, default_input_include_prob=.5, input_include_probs=None,
            default_input_scale=2., input_scales=None, alpha = 1., scale_ensemble=False,
            dont_drop_input = None):
        """
        During training, each input to each layer is randomly included or excluded
        for each example. The probability of inclusion is independent for each input
        and each example. Each layer uses "default_input_include_prob" unless that
        layer's name appears as a key in input_include_probs, in which case the input
        inclusion probability is given by the corresponding value.

        Each feature is also multiplied by a scale factor. The scale factor for each
        layer's input scale is determined by the same scheme as the input probabilities.
        """
        if dont_drop_input is None:
            dont_drop_input = []

        if input_include_probs is None:
            input_include_probs = {}

        if input_scales is None:
            input_scales = {}

        self.__dict__.update(locals())
        del self.self

    def __call__(self, model, X, Y, ** kwargs):
        Y_hat, Y_hat_e = model.lone_ranger_dropout_fprop(X, default_input_include_prob=self.default_input_include_prob,
                input_include_probs=self.input_include_probs, default_input_scale=self.default_input_scale,
                input_scales=self.input_scales, scale_ensemble=self.scale_ensemble, dont_drop_input = self.dont_drop_input
                )

        assert hasattr(Y_hat, 'owner')
        owner = Y_hat.owner
        assert owner is not None
        op = owner.op
        if isinstance(op, Print):
            assert len(owner.inputs) == 1
            Y_hat, = owner.inputs
            owner = Y_hat.owner
            op = owner.op
        assert isinstance(op, T.nnet.Softmax)
        z ,= owner.inputs
        assert z.ndim == 2

        z_weight = Y_hat - Y_hat_e
        z_weight = block_gradient(z_weight)
        neg = z_weight * z
        neg = neg.sum(axis=1).mean()

        z = z - z.max(axis=1).dimshuffle(0, 'x')
        log_prob = z - T.log(T.exp(z).sum(axis=1).dimshuffle(0, 'x'))
        # we use sum and not mean because this is really one variable per row
        log_prob_of = (Y * log_prob).sum(axis=1)
        assert log_prob_of.ndim == 1
        log_prob_of = log_prob_of.mean()

        return -(log_prob_of + self.alpha * neg)

class LoneRanger(MLP):

    def lone_ranger_dropout_fprop(self, state_below, default_input_include_prob=0.5, input_include_probs=None,
        default_input_scale=2., input_scales=None, scale_ensemble=False, dont_drop_input = None):
        """
        state_below: The input to the MLP

        Returns the output of the MLP, when applying dropout to the input and intermediate layers.
        Each input to each layer is randomly included or excluded
        for each example. The probability of inclusion is independent for each input
        and each example. Each layer uses "default_input_include_prob" unless that
        layer's name appears as a key in input_include_probs, in which case the input
        inclusion probability is given by the corresponding value.

        Each feature is also multiplied by a scale factor. The scale factor for each
        layer's input scale is determined by the same scheme as the input probabilities.

        """

        if dont_drop_input is None:
            dont_drop_input = []

        warnings.warn("dropout should be implemented with fixed_var_descr to"
                " make sure it works with BGD, this is just a hack to get it"
                "working with SGD")

        if input_include_probs is None:
            input_include_probs = {}

        if input_scales is None:
            input_scales = {}

        assert all(layer_name in self.layer_names for layer_name in input_include_probs)
        assert all(layer_name in self.layer_names for layer_name in input_scales)

        theano_rng = MRG_RandomStreams(self.rng.randint(2**15))

        state_below = (state_below, state_below)

        for layer in self.layers:
            layer_name = layer.layer_name

            if layer_name in input_include_probs:
                include_prob = input_include_probs[layer_name]
            else:
                include_prob = default_input_include_prob

            if layer_name in input_scales:
                scale = input_scales[layer_name]
            else:
                scale = default_input_scale

            if layer_name not in dont_drop_input:
                state_below = self.apply_lone_ranger_dropout(state=state_below,
                    include_prob=include_prob,
                    theano_rng=theano_rng,
                    scale=scale, scale_ensemble=scale_ensemble)

            state_below = (layer.fprop(state_below[0]), layer.fprop(state_below[1]))

        return state_below

    def apply_lone_ranger_dropout(self, state, include_prob, scale, theano_rng,
            scale_ensemble=False):
        if include_prob in [None, 1.0, 1]:
            return state
        assert scale is not None
        assert isinstance(state, tuple)
        assert len(state) == 2
        lone_ranger_state, ensemble_state = state
        if isinstance(lone_ranger_state, tuple) or isinstance(ensemble_state, tuple):
            raise NotImplementedError()
        d = theano_rng.binomial(p=include_prob, size=lone_ranger_state.shape, dtype=lone_ranger_state.dtype)
        ensemble_scale = 1
        if scale_ensemble:
            ensemble_scale = scale
        return (lone_ranger_state * d * scale, ensemble_state * (1 -d) * ensemble_scale)