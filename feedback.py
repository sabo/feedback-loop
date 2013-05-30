"""Feedback-Loop is a twitter bot that chops up the twitter feed of whomever it
follows, then spits it back at them. 
Something something detournement something."""
import tweepy
from tweepy.error import TweepError
import random
import shelve
import re
import atexit
import logging
from time import sleep
from datetime import datetime

userdb = shelve.open("users", writeback=True)
markovdb = shelve.open("markov", writeback=True)

logging.basicConfig(filename='output.log', level=logging.DEBUG)

#Reggie the stupid simple user-matching regex
reggie = re.compile("^@")

@atexit.register
def close_dbs():
    """Closes stuff."""
    userdb.close()
    markovdb.close()

class FeedbackLoop():
    def __init__(self):
        """Initializes the twitter api""" 
        consumer_key = ''
        consumer_secret = ''
        access_token_key = ''
        access_token_secret = ''
        
        auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
        auth.set_access_token(access_token_key, access_token_secret)
        
        self.api = tweepy.API(auth)
        self.friends_ids = self.api.friends_ids()
        self.api_cutoff = 5
        logging.info("API initialized")
 
    def check_rate(self, resource_type, resource):
        """Checks usage for given resources, and sleeps the amount of seconds 
        we should sleep until it resets."""
        stat = self.api.rate_limit_status()
        resource_info = stat["resources"][resource_type][resource]

        if resource_info['remaining'] < self.api_cutoff:
            reset_time = datetime.utcfromtimestamp(resource_info["reset"])
            remaining_time = \
                (reset_time - datetime.utcnow()).total_seconds() + 10

            if remaining_time > 0:
                logging.info("API hits exhausted. Waking up in {0} seconds"
                        .format(remaining_time))
                sleep(remaining_time)
                logging.info( "Waking up.")
                #Just to be sure.
                self.check_rate(resource_type, resource)

    def all_tweets(self, user_id, stop_at = 50):
        """Gets tweets for a given username. 
        Stops at 50 pages by default"""
        self.check_rate("statuses", "/statuses/user_timeline")
        logging.info("Getting timeline for {0}".format(user_id))
        t = self.api.user_timeline(user_id, count=100, include_rts=False)
        logging.info("Page 1: got {0} tweets".format(len(t)))
        n = 2
        while n < stop_at:
            self.check_rate("statuses", "/statuses/user_timeline")
            results = self.api.user_timeline(user_id, count=100, page=n,
                    include_rts=False)
            logging.info("Page {0}: got {1} tweets".format(n, len(results)))
            if results:
                t.extend(results)
            else:
                logging.info("Got no results; bailing.")
                break
            n += 1
            sleep(0.25)
        return t

    def updated_tweets(self, user_id, from_tweet_id, stop_at = 50):
        """Gets all tweets for a give user id, since a given tweet id."""
        self.check_rate("statuses", "/statuses/user_timeline")
        logging.info("Updating timeline for {0}".format(user_id))
        t = self.api.user_timeline(user_id, count=100, include_rts=False,
                since_id = from_tweet_id)
        logging.info("Page 1: got {0} tweets".format(len(t)))
        if not t:
            logging.info("Up to date for {0}".format(user_id))
            return None
        n = 2
        while n < stop_at:
            self.check_rate("statuses", "/statuses/user_timeline")
            results = self.api.user_timeline(user_id, count=100, page=n,
                    include_rts=False, since_id=from_tweet_id)
            logging.info("Page {0}: got {1} tweets".format(n, len(results)))
            if results:
                t.extend(results)
            else:
                break
            n += 1
            sleep(0.25)
        return t

    def split_words(self, text):
        """Breaks words into pairs and adds them to a database. "text" can be 
        any unicode string. Markov should be either an empty dict, or a 
        prepopulated markov dict."""
        text = text.encode('ascii', 'replace')
        words_dirty = text.split()
        words = []
        #Reggie matches usernames. So this just makes a list of words in a
        #tweet without usernames.
        for word in words_dirty:
            if not reggie.match(word):
                words.append(word)
        index = 0
        while index < (len(words) - 1):
            key = words[index]
            if key in markovdb:
                markovdb[key].append(words[index + 1])
            else:
                markovdb[key] = [words[index + 1]]
            index += 1
    
    def build_db(self):
        """Builds and/or updates our databases."""
        #Update our list of friends.
        logging.info("Updating database...")
        self.friends_ids = self.api.friends_ids()
        for f_id in self.friends_ids:
            if str(f_id) in userdb:
                tweets = self.updated_tweets(f_id, userdb[str(f_id)])
            else:
                tweets = self.all_tweets(f_id)
            if tweets:
                userdb[str(f_id)] = tweets[0].id
                for tweet in tweets:
                    self.split_words(tweet.text)
        logging.info("Update complete.")
    
    def generate_tweet(self, length=140):
        """Generates a tweet of given length."""
        word = random.choice(markovdb.keys())
        out = word
        while True:
            try:
                step = random.choice(markovdb[word])
            except KeyError:
                out = self.generate_tweet()
                return out
            if (len(out) + len(step)) >= length:
                return out
            out += " " + step
            word = step

    def retweet_random_friend(self):
        """retweet a random tweet from a random followed entity"""
        target_id = random.choice(self.friends_ids)
        self.check_rate("statuses", "/statuses/user_timeline")
        try:
            target_timeline = \
                self.api.user_timeline(id=target_id, include_rts=False)
        except TweepError:
            return False
    
        target_tweet = random.choice(target_timeline)

        try:
            self.api.retweet(target_tweet.id)
        except TweepError:
            return False
        return True

    def follow_random_suggested_user(self):
        """Picks a random user suggested by twitter, then follows them."""
        suggested_cat = random.choice(self.api.suggested_categories())
        suggested_user = \
            random.choice(self.api.suggested_users(suggested_cat.slug))
        try:
            self.api.create_friendship(suggested_user.id)
            logging.info("Followed user {0}".format(suggested_user.screen_name))
        except TweepError:
            return False
        return True

    def resend_random_tweet(self):
        """Picks a tweet from a random followee, then tweets it at another
        random followee."""
        donor = random.choice(self.friends_ids)
        recipient = random.choice(self.friends_ids)
        if donor == recipient:
            return False
        r_lookup = self.api.get_user(recipient)
        try:
            self.check_rate("statuses", "/statuses/user_timeline")
            d_timeline = \
                    self.api.user_timeline(id=donor, include_rts=False)
        except TweepError:
            return False
        organ = random.choice(d_timeline)
        tweet = ".@{0} RT @{1} {2}".format(r_lookup.screen_name,
                organ.user.screen_name, organ.text)
        if len(tweet) > 140:
            tweet = tweet[0:137] + "..."
        try:
            self.api.update_status(tweet[0:140])
        except TweepError:
            return False

        return True

    def send_generated_tweet(self):
        """Sends a generated tweet of max length"""
        msg = self.generate_tweet()
        try:
            self.api.update_status(msg)
            return True
        except TweepError:
            return False

    def send_generated_shorter_tweet(self):
        """Sends a shorter generated tweet."""
        length = random.randint(70, 139)
        msg = self.generate_tweet(length)
        try:
            self.api.update_status(msg)
            return True
        except TweepError:
            return False

    def weighted_choice(self, choices):
        """Does what it says on the tin -- weighted choice. 'choices' should be
        a list in the format (choice, weight).
        Ripped off from http://stackoverflow.com/a/3679747"""
        total = sum(w for c, w in choices)
        r = random.uniform(0, total)
        upto = 0
        for c, w in choices:
            if upto + w > r:
                return c
            upto += w
        assert False, "Shouldn't get here"
    
    def act_like_a_person(self, override = False):
        """A crappy approximation of human behavior. Sleeps for some time, then
        performs an action."""
        #base number of 15-minute intervals we should sleep for
        m_base = (random.randint(1, 8))*15
        #Fuzz factorfor the minutes
        fuzz_min = random.randint(0, 10)
        #Total minutes we should sleep for
        minutes = m_base + fuzz_min
        #Fuzz factor for seconds
        fuzz_sec = random.randint(0, 59)
        #Convert minutes to seconds, add the fuzz, then sleep.
        if not override:
            sleep((minutes * 60) + fuzz_sec)
        else:
            logging.debug(
                "Would sleep for {0} minutes {1} seconds. Performing action..."
                .format(minutes, fuzz_sec))

        #This is a weighted list of things that our bot can do.
        #Weights should sum to 1.
        actions = [(self.build_db, 0.01), \
                   (self.retweet_random_friend, 0.15), \
                   (self.follow_random_suggested_user, 0.04), \
                   (self.resend_random_tweet, 0.30), \
                   (self.send_generated_tweet, 0.30), \
                   (self.send_generated_shorter_tweet, 0.20)]

        action = self.weighted_choice(actions)
        logging.info("Performing action: {0}".format(action.__name__))
        action()

def main():
    liam = FeedbackLoop()
    while True:
        liam.act_like_a_person()

if __name__ == "__main__":
    main()
