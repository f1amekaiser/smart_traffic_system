import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.metrics import accuracy_score, classification_report
import joblib

# =========================
# LOAD DATA
# =========================
df = pd.read_csv("../data/traffic_data.csv")

# =========================
# DATA CLEANING
# =========================

df = df[df["ns_queue"] >= 0]
df = df[df["ew_queue"] >= 0]

df.fillna(0, inplace=True)

# =========================
# FEATURE ENGINEERING
# =========================

df["ns_speed"] = df["ns_speed"] / 15.0
df["ew_speed"] = df["ew_speed"] / 15.0

df["total_queue"] = df["ns_queue"] + df["ew_queue"]
df["total_wait"] = df["ns_wait"] + df["ew_wait"]
df["queue_ratio"] = (df["ns_queue"] + 1) / (df["ew_queue"] + 1)
df["wait_ratio"] = (df["ns_wait"] + 1) / (df["ew_wait"] + 1)

# =========================
# SPLIT DATA
# =========================
X = df.drop("decision", axis=1)
y = df["decision"]

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# =========================
# MODEL + TUNING
# =========================

param_grid = {
    "n_estimators": [100, 150, 200],
    "max_depth": [8, 10, 15],
    "min_samples_split": [2, 5],
    "min_samples_leaf": [1, 2]
}

rf = RandomForestClassifier(random_state=42)

grid = GridSearchCV(
    rf,
    param_grid,
    cv=3,
    n_jobs=-1,
    verbose=1
)

grid.fit(X_train, y_train)

best_model = grid.best_estimator_

print("Best Params:", grid.best_params_)

# =========================
# EVALUATION
# =========================

y_pred = best_model.predict(X_test)

print("Accuracy:", accuracy_score(y_test, y_pred))
print("\nReport:\n", classification_report(y_test, y_pred))

# =========================
# FEATURE IMPORTANCE
# =========================

importances = best_model.feature_importances_
features = X.columns

importance_df = pd.DataFrame({
    "feature": features,
    "importance": importances
}).sort_values(by="importance", ascending=False)

print("\nFeature Importance:\n", importance_df)

# =========================
# SAVE MODEL
# =========================

joblib.dump(best_model, "traffic_rf_model.pkl")

print("\nModel saved as traffic_rf_model.pkl")