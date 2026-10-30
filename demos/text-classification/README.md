# text-classification

A text-classification problem on the REAL 20 Newsgroups benchmark (the classic scikit-learn / NLP dataset). You edit `src/model.py` — `fit(texts, y)` then `predict(texts)`, which receive raw document strings — to maximize held-out **accuracy** over the 20 categories (**higher is better**).

A plain TF-IDF + logistic-regression baseline sits around **0.674**; engineering the text features (word/char n-grams, sublinear TF, `min_df`/`max_df`, feature selection) plus a stronger linear model pushes accuracy toward **~0.80+**. Real data, pure CPU, ~tens of seconds per evaluation, so many candidates can be tried.

## Background

The task is the standard 20 Newsgroups text classification benchmark (scikit-learn `fetch_20newsgroups`): 11,314 train / 7,532 test documents across 20 categories, with headers, footers, and quotes removed — the standard *harder* setting. The read-only evaluator loads the standard train/test split, calls `model.fit(train_texts, train_y)` then `model.predict(test_texts)`, and reports test accuracy (a failed candidate scores 0.0).

The baseline `src/model.py` is a `TfidfVectorizer` + `LogisticRegression` pipeline (~0.674). Run the baseline first to establish the starting accuracy, then iterate on the vectorizer + model. Key levers: word AND character n-grams, `sublinear_tf`, tuned `min_df`/`max_df`, English stopwords, chi2 / mutual-info feature selection, and stronger linear models (LinearSVC, ComplementNB) with class weighting.

## What the agent optimizes

- **Edits `src/model.py`** — `fit(texts, y)` and `predict(texts)`; controls the vectorizer/feature engineering and the classifier.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed harness: train/test split, scoring, and environment.
- **Metric:** `accuracy` (higher is better), from `run.log`.

## Ideas to try

- Run the TF-IDF + logistic baseline to establish the starting accuracy (~0.67).
- Add word bigrams (`ngram_range=(1,2)`), `sublinear_tf=True`, and tune `min_df`/`max_df`.
- Swap the classifier to LinearSVC (often strong on TF-IDF text).
- Add character n-grams (`analyzer='char_wb'`, `ngram_range=(3,5)`) and combine with word features.
- Add chi2 feature selection and tune the regularization strength.
- Try English stopword removal, ComplementNB, and class weighting.

## References

- [20 Newsgroups dataset (scikit-learn)](https://scikit-learn.org/stable/datasets/real_world.html#newsgroups-dataset)
- [Document classification on 20 Newsgroups (scikit-learn example)](https://scikit-learn.org/stable/auto_examples/text/plot_document_classification_20newsgroups.html)
