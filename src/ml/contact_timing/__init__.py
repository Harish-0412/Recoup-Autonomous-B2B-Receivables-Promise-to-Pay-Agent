"""Contact-timing optimizer: when to send a reminder so it gets answered.

A segmented Beta-Bernoulli Thompson Sampling bandit over 15 send-time arms
(5 weekdays x 3 dayparts). Segments come from observable customer features
only -- the generator's hidden archetype is never a feature, and a test
asserts the training frame cannot reach it.

Custom lightweight implementation rather than pymab: the whole posterior is a
dict of (alpha, beta) counts, which keeps the artifact inspectable, the
online update a one-line addition, and the dependency list unchanged.
"""
