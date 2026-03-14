import pandas as pd
import numpy as np
import pickle #saves model
import re
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import LabelEncoder
import xgboost as xgb
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TicketSentimentAnalyzer:
    """
    Multi-task XGBoost model for ticket classification:
    - Issue Type Classification
    - Priority Prediction
    - Sentiment Analysis
    """
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            max_features=5000,
            ngram_range=(1, 3),
            min_df=2,
            max_df=0.9,
            strip_accents='unicode',
            lowercase=True
        )
        
        self.issue_encoder = LabelEncoder()
        self.priority_encoder = LabelEncoder()
        self.sentiment_encoder = LabelEncoder()
        
        self.issue_model = None
        self.priority_model = None
        self.sentiment_model = None
        
    def preprocess_text(self, text):
        #Clean and preprocess text
        if pd.isna(text):
            return ""
        
        text = str(text).lower()
        text = re.sub(r'hi support,?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        return text
    
    def extract_sentiment_from_text(self, text):
        """
        Extract sentiment based on keywords and patterns
        Returns: Positive, Neutral, Negative, Urgent
        """
        text_lower = text.lower()
        
        # Urgent/Critical indicators
        urgent_keywords = ['urgent', 'critical', 'emergency', 'immediately', 'asap', 
                          'cannot', 'can\'t', 'failed', 'not working', 'broken', 'help']
        
        # Negative sentiment
        negative_keywords = ['frustrated', 'disappointed', 'angry', 'worst', 
                            'terrible', 'horrible', 'unacceptable', 'issue', 'problem']
        
        # Positive sentiment
        positive_keywords = ['thank', 'appreciate', 'great', 'excellent', 
                           'wonderful', 'love', 'perfect', 'question']
        
        urgent_count = sum(1 for word in urgent_keywords if word in text_lower)
        negative_count = sum(1 for word in negative_keywords if word in text_lower)
        positive_count = sum(1 for word in positive_keywords if word in text_lower)
        
        # Priority: Urgent > Negative > Positive > Neutral
        if urgent_count >= 2 or 'cannot' in text_lower or 'can\'t' in text_lower:
            return 'Urgent'
        elif negative_count > positive_count:
            return 'Negative'
        elif positive_count > 0:
            return 'Positive'
        else:
            return 'Neutral'
    
    def prepare_data(self, csv_path):
        """Load and prepare training data"""
        logger.info(f"Loading data from {csv_path}")
        
        df = pd.read_csv(csv_path)
        logger.info(f"Loaded {len(df)} records")
        
        # Combine subject and description for better context
        df['combined_text'] = (
            df['Ticket_Subject'].fillna('') + ' ' + 
            df['Ticket_Description'].fillna('')
        )
        
        df['processed_text'] = df['combined_text'].apply(self.preprocess_text)
        
        # Extract sentiment from text
        df['Sentiment'] = df['processed_text'].apply(self.extract_sentiment_from_text)
        
        logger.info("\nData Distribution:")
        logger.info(f"Issue Categories:\n{df['Issue_Category'].value_counts()}")
        logger.info(f"\nPriority Levels:\n{df['Priority_Level'].value_counts()}")
        logger.info(f"\nSentiment:\n{df['Sentiment'].value_counts()}")
        
        return df
    
    def train(self, csv_path, test_size=0.2, random_state=42):
        """Train all three models"""
        
        df = self.prepare_data(csv_path)
        
        # Encode labels
        df['issue_encoded'] = self.issue_encoder.fit_transform(df['Issue_Category'])
        df['priority_encoded'] = self.priority_encoder.fit_transform(df['Priority_Level'])
        df['sentiment_encoded'] = self.sentiment_encoder.fit_transform(df['Sentiment'])
        
        # Split data
        X_train, X_test, y_train_df, y_test_df = train_test_split(
            df['processed_text'],
            df[['issue_encoded', 'priority_encoded', 'sentiment_encoded']],
            test_size=test_size,
            random_state=random_state,
            stratify=df['issue_encoded']
        )
        
        logger.info(f"\nTrain size: {len(X_train)}, Test size: {len(X_test)}")
        
        # TF-IDF Vectorization
        logger.info("Vectorizing text with TF-IDF...")
        X_train_vec = self.vectorizer.fit_transform(X_train)
        X_test_vec = self.vectorizer.transform(X_test)
        
        # Train Issue Type Classifier
        logger.info("\n" + "="*50)
        logger.info("Training Issue Type Classifier")
        logger.info("="*50)
        
        self.issue_model = xgb.XGBClassifier(
            objective='multi:softmax',
            num_class=len(self.issue_encoder.classes_),
            max_depth=6,
            learning_rate=0.1,
            n_estimators=200,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=random_state,
            eval_metric='mlogloss'
        )
        
        self.issue_model.fit(X_train_vec, y_train_df['issue_encoded'])
        y_pred_issue = self.issue_model.predict(X_test_vec)
        
        logger.info(f"\nIssue Type Accuracy: {accuracy_score(y_test_df['issue_encoded'], y_pred_issue):.4f}")
        logger.info("\nClassification Report:")
        logger.info(classification_report(
            y_test_df['issue_encoded'], 
            y_pred_issue,
            target_names=self.issue_encoder.classes_
        ))
        
        # Train Priority Predictor
        logger.info("\n" + "="*50)
        logger.info("Training Priority Predictor")
        logger.info("="*50)
        
        self.priority_model = xgb.XGBClassifier(
            objective='multi:softmax',
            num_class=len(self.priority_encoder.classes_),
            max_depth=5,
            learning_rate=0.1,
            n_estimators=150,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=random_state,
            eval_metric='mlogloss'
        )
        
        self.priority_model.fit(X_train_vec, y_train_df['priority_encoded'])
        y_pred_priority = self.priority_model.predict(X_test_vec)
        
        logger.info(f"\nPriority Accuracy: {accuracy_score(y_test_df['priority_encoded'], y_pred_priority):.4f}")
        logger.info("\nClassification Report:")
        logger.info(classification_report(
            y_test_df['priority_encoded'], 
            y_pred_priority,
            target_names=self.priority_encoder.classes_
        ))
        
        # Train Sentiment Analyzer
        logger.info("\n" + "="*50)
        logger.info("Training Sentiment Analyzer")
        logger.info("="*50)
        
        self.sentiment_model = xgb.XGBClassifier(
            objective='multi:softmax',
            num_class=len(self.sentiment_encoder.classes_),
            max_depth=5,
            learning_rate=0.1,
            n_estimators=150,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=random_state,
            eval_metric='mlogloss'
        )
        
        self.sentiment_model.fit(X_train_vec, y_train_df['sentiment_encoded'])
        y_pred_sentiment = self.sentiment_model.predict(X_test_vec)
        
        logger.info(f"\nSentiment Accuracy: {accuracy_score(y_test_df['sentiment_encoded'], y_pred_sentiment):.4f}")
        logger.info("\nClassification Report:")
        logger.info(classification_report(
            y_test_df['sentiment_encoded'], 
            y_pred_sentiment,
            target_names=self.sentiment_encoder.classes_
        ))
        
        logger.info("\n" + "="*50)
        logger.info("Training Complete!")
        logger.info("="*50)
    
    def predict(self, text, return_probabilities=False):
        """
        Predict issue type, priority, and sentiment for given text
        
        Returns:
            dict with predictions and confidence scores
        """
        if not all([self.issue_model, self.priority_model, self.sentiment_model]):
            raise ValueError("Models not trained. Call train() first or load models.")
        
        # Preprocess
        processed = self.preprocess_text(text)
        
        # Vectorize
        text_vec = self.vectorizer.transform([processed])
        
        # Predict
        issue_pred = self.issue_model.predict(text_vec)[0]
        priority_pred = self.priority_model.predict(text_vec)[0]
        sentiment_pred = self.sentiment_model.predict(text_vec)[0]
        
        # Get probabilities
        issue_proba = self.issue_model.predict_proba(text_vec)[0]
        priority_proba = self.priority_model.predict_proba(text_vec)[0]
        sentiment_proba = self.sentiment_model.predict_proba(text_vec)[0]
        
        result = {
            'issue_type': self.issue_encoder.inverse_transform([issue_pred])[0],
            'issue_confidence': float(max(issue_proba)),
            'priority': self.priority_encoder.inverse_transform([priority_pred])[0],
            'priority_confidence': float(max(priority_proba)),
            'sentiment': self.sentiment_encoder.inverse_transform([sentiment_pred])[0],
            'sentiment_confidence': float(max(sentiment_proba))
        }
        
        if return_probabilities:
            result['issue_probabilities'] = {
                cls: float(prob) 
                for cls, prob in zip(self.issue_encoder.classes_, issue_proba)
            }
            result['priority_probabilities'] = {
                cls: float(prob)
                for cls, prob in zip(self.priority_encoder.classes_, priority_proba)
            }
            result['sentiment_probabilities'] = {
                cls: float(prob)
                for cls, prob in zip(self.sentiment_encoder.classes_, sentiment_proba)
            }
        
        return result
    
    def save_models(self, prefix='ticket_model'):
        """Save all models and encoders"""
        logger.info(f"Saving models with prefix: {prefix}")
        
        # Save models
        self.issue_model.save_model(f'{prefix}_issue.json')
        self.priority_model.save_model(f'{prefix}_priority.json')
        self.sentiment_model.save_model(f'{prefix}_sentiment.json')
        
        # Save vectorizer and encoders
        with open(f'{prefix}_vectorizer.pkl', 'wb') as f:
            pickle.dump(self.vectorizer, f)
        
        with open(f'{prefix}_encoders.pkl', 'wb') as f:
            pickle.dump({
                'issue': self.issue_encoder,
                'priority': self.priority_encoder,
                'sentiment': self.sentiment_encoder
            }, f)
        
        logger.info("Models saved successfully!")
    
    def load_models(self, prefix='ticket_model'):
        """Load all models and encoders"""
        logger.info(f"Loading models with prefix: {prefix}")
        
        # Load models
        self.issue_model = xgb.XGBClassifier()
        self.issue_model.load_model(f'{prefix}_issue.json')
        
        self.priority_model = xgb.XGBClassifier()
        self.priority_model.load_model(f'{prefix}_priority.json')
        
        self.sentiment_model = xgb.XGBClassifier()
        self.sentiment_model.load_model(f'{prefix}_sentiment.json')
        
        # Load vectorizer and encoders
        with open(f'{prefix}_vectorizer.pkl', 'rb') as f:
            self.vectorizer = pickle.load(f)
        
        with open(f'{prefix}_encoders.pkl', 'rb') as f:
            encoders = pickle.load(f)
            self.issue_encoder = encoders['issue']
            self.priority_encoder = encoders['priority']
            self.sentiment_encoder = encoders['sentiment']
        
        logger.info("Models loaded successfully!")


def main():
    """Train the model"""
    
    analyzer = TicketSentimentAnalyzer()
    
    # Train on dataset
    analyzer.train('support_ticket_dataset.csv', test_size=0.2)
    
    # Save models
    analyzer.save_models('ticket_model')
    
    # Test predictions
    print("\n" + "="*70)
    print("TESTING PREDICTIONS")
    print("="*70)
    
    test_cases = [
        "I cannot login to my account. This is urgent!",
        "My payment failed and I was charged twice.",
        "How do I upgrade my subscription plan?",
        "The app keeps crashing when I open settings.",
        "I received a suspicious email claiming to be from support."
    ]
    
    for test_text in test_cases:
        print(f"\nText: {test_text}")
        result = analyzer.predict(test_text)
        print(f"Issue: {result['issue_type']} (confidence: {result['issue_confidence']:.2%})")
        print(f"Priority: {result['priority']} (confidence: {result['priority_confidence']:.2%})")
        print(f"Sentiment: {result['sentiment']} (confidence: {result['sentiment_confidence']:.2%})")


if __name__ == "__main__":
    main()
