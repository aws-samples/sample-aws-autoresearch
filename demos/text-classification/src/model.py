"""20-Newsgroups text-classification candidate — the AI Researcher edits THIS file freely.

Provide:
  fit(texts, y)   — texts is a list of raw document strings, y is (n,) integer class labels.
  predict(texts)  — list of strings; return predicted class labels (length m).

The objective (scored by evaluate.py, which you may NOT edit) is to MAXIMIZE held-out accuracy on
the real 20-Newsgroups test split. The baseline is a plain TF-IDF + logistic-regression pipeline.
Engineer the text features (n-grams, sublinear TF, min_df/max_df, stopwords, char n-grams, feature
selection) and/or change the classifier (LinearSVC, ComplementNB, ...). scikit-learn is available.
"""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

_pipe = None


def fit(texts, y):
    global _pipe
    _pipe = Pipeline([
        ("tfidf", TfidfVectorizer()),
        ("clf", LogisticRegression(max_iter=1000)),
    ])
    _pipe.fit(list(texts), list(y))


def predict(texts):
    return _pipe.predict(list(texts))
