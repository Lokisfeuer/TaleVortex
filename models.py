# models.py
import os

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from fpdf import FPDF
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.types import PickleType
from sqlalchemy.orm import validates
from werkzeug.utils import secure_filename

from openai_client import call_openai

db = SQLAlchemy()


class User(db.Model, UserMixin):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    stories = db.relationship('Story', backref='user', lazy=True)


class Story(db.Model):
    __tablename__ = 'story'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    units = db.relationship('Unit', backref='story', lazy=True)
    undefined_names = db.Column(MutableList.as_mutable(PickleType), default=[])
    setting_and_style = db.Column(db.Text, nullable=False)
    main_challenge = db.Column(db.Text, nullable=False)
    board_game_data = db.Column(MutableDict.as_mutable(db.JSON), default={
            'texts': {},
            'images': {}
        })

    def __getitem__(self, unit_name):
        return self.get_unit_by_name(unit_name)

    def __len__(self):
        return len(self.units)

    def __iter__(self):
        return iter(self.units)

    def add_unit(self, unit):
        unit.story_id = self.id
        db.session.add(unit)
        db.session.commit()

    def get_unit_by_name(self, name):
        return next((unit for unit in self.units if unit.name.lower() == name.lower()), None)

    def to_json(self):
        '''
        Serialize the story and its units into a JSON-friendly dictionary.
        '''
        story_dict = {
            'id': self.id,
            'name': self.name,
            'user_id': self.user_id,
            'undefined_names': self.undefined_names,
            'setting_and_style': self.setting_and_style,
            'main_challenge': self.main_challenge,
            'units': [unit.to_json() for unit in self.units]
        }
        return story_dict

    def to_text_list(self):
        lines = []
        lines.append(f"Story Name: {self.name}")
        lines.append(f"Setting and Style: {self.setting_and_style}")
        lines.append(f"Main Challenge: {self.main_challenge}")
        lines.append("")  # Blank line for separation

        unit_dict = {unit.name: unit for unit in self.units}

        for unit in self.units:
            unit_name = unit.name or ''
            unit_header = f"{unit.unit_type}: {unit_name}"
            lines.append(unit_header)

            for feature_name, value in unit.features.items():
                value_str = ''
                if isinstance(value, list):
                    related_units = []
                    for item in value:
                        if isinstance(item, str):
                            related_unit = unit_dict.get(item)
                            if related_unit:
                                related_unit_name = related_unit.name or item
                                related_units.append(f"{related_unit.unit_type}: {related_unit_name}")
                            elif item in self.undefined_names:
                                related_units.append(f"Unset name: {item}")
                            else:
                                related_units.append(f"Really undefined: {item}")
                        else:
                            related_units.append(str(item))
                    value_str = '; '.join(related_units)
                elif isinstance(value, str):
                    value_str = value
                elif isinstance(value, bool):
                    value_str = 'Yes' if value else 'No'
                elif isinstance(value, float):
                    value_str = str(round(value, 2))
                else:
                    value_str = str(value)
                lines.append(f"  {feature_name}: {value_str}")

            lines.append("")  # Blank line between units

        return "\n".join(lines)

    def to_pdf(self, filename='story.pdf'):
        from fpdf import FPDF
        story_text = self.to_full_text()
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        pdf.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
        pdf.set_font('DejaVu', '', 16)
        # pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, 'Story', ln=True, align='C')
        # pdf.set_font('Arial', '', 12)
        pdf.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
        pdf.set_font('DejaVu', '', 12)
        pdf.multi_cell(0, 10, story_text)
        pdf.output(secure_filename(filename))
        print('PDF created successfully')

    def create_full_story_prompt(self):
        '''
        Create a prompt for generating the full story text.
        '''
        prompt = f"""Please write a complete, engaging, and cohesive story based on the following details. Incorporate all the elements provided, and write as an actual author would write a story.

Setting and Style:
{self.setting_and_style}

Main Challenge:
{self.main_challenge}

Units:
{self.to_text_list()}

Ensure that the story flows naturally and includes dialogue, descriptions, and character interactions as appropriate. Do not include any lists or bullet points. Write the story in continuous prose.
"""
        return prompt

    def to_full_text(self):
        '''
        Generate the full text of the story using OpenAI API.
        '''
        # Prepare the prompt
        prompt = self.create_full_story_prompt()

        try:
            messages = [
                {
                    "role": "system",
                    "content": "You are a talented author crafting immersive and engaging stories."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
            full_story = call_openai(messages, model="gpt-4o")  # TODO: use "o1-mini" instead, it is much better

            return full_story
        except Exception as e:
            # Handle exceptions, perhaps log the error
            print(f"Error generating full story: {e}")
            return None

    def create_board_game_prompt(self):  # TODO: Redo this prompt, it is not good.
        prompt = f"""
Based on the following story information, generate the necessary data for creating a board adventure game. Provide the data in JSON format with the following structure:

{{
    "setting_and_style": "...",
    "challenge_or_goal": "...",
    "core_secrets": "...",
    "start_description": "...",
    "chronology": "...",
    "locations": [
        {{
            "name": "...",
            "description": "...",
            "events": "...",
            "npcs": "...",
            "investigation": "..."
        }},
        // Repeat for each location
    ],
    "npcs": [
        {{
            "name": "...",
            "description": "..."
        }},
        // Repeat for each NPC
    ]
}}

Story Setting and Style:
{self.setting_and_style}

Main Challenge:
{self.main_challenge}

Existing Units:
{self.to_text_list()}

Please ensure the JSON is properly formatted.
"""
        return prompt

    def generate_npc_pdf(self, output_path):
        from math import ceil

        pdf = FPDF('P', 'mm', (74, 105))  # A7 size (width, height)

        texts = self.board_game_data.get('texts', {})
        images = self.board_game_data.get('images', {})

        npcs = []
        index = 0
        while True:
            desc_key = f'npc_{index}_description'
            img_key = f'npc_{index}_image'
            img_existing_key = f'npc_{index}_image_existing'

            if desc_key not in texts:
                break  # No more NPCs

            description = texts.get(desc_key, '')
            image_filename = images.get(img_key)

            if not image_filename:
                # Try to get existing image
                image_filename = texts.get(img_existing_key, '')

            image_path = os.path.join('static', 'uploads', secure_filename(image_filename)) if image_filename else None

            npcs.append({
                'description': description,
                'image_path': image_path
            })
            index += 1

        for npc in npcs:
            # Front side (image)
            pdf.add_page()
            if npc['image_path'] and os.path.exists(secure_filename(npc['image_path'])):
                pdf.image(secure_filename(npc['image_path']), x=0, y=0, w=74, h=105)
            else:
                pdf.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
                pdf.set_font('DejaVu', '', 16)
                pdf.cell(0, 10, 'Image not available', ln=True, align='C')

            # Back side (text)
            pdf.add_page()
            pdf.set_auto_page_break(auto=False)
            pdf.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
            max_font_size = 12  # Start with maximum font size
            min_font_size = 6  # Minimum font size to attempt
            x0, y0 = 10, 10
            available_width = 54  # Width available for text (74 - 2*10 mm margins)
            available_height = 105 - y0 - 10  # Height available for text (page height - top margin - bottom margin)
            font_size = max_font_size

            # Try to find the largest font size where the text fits
            while font_size >= min_font_size:
                pdf.set_font('DejaVu', '', font_size)
                line_height = pdf.font_size * 1.2  # Approximate line height
                text_lines = split_text_to_fit(pdf, npc['description'], available_width)
                total_height = line_height * len(text_lines)

                if total_height <= available_height:
                    break  # Text fits
                else:
                    font_size -= 1  # Decrease font size and try again

            # Set the font to the determined size and write the text
            pdf.set_font('DejaVu', '', font_size)
            pdf.set_xy(x0, y0)
            pdf.multi_cell(available_width, line_height, npc['description'], align='C')

        pdf.output(output_path)

    def generate_location_pdf(self, output_path):
        from math import ceil

        pdf = FPDF('P', 'mm', 'A5')  # A5 size (148mm x 210mm)

        texts = self.board_game_data.get('texts', {})
        images = self.board_game_data.get('images', {})

        locations = []
        index = 0
        while True:
            # Collect all keys for the current location
            desc_key = f'location_{index}_description'
            events_key = f'location_{index}_events'
            npcs_key = f'location_{index}_npcs'
            investigation_key = f'location_{index}_investigation'
            img_key = f'location_{index}_image'
            img_existing_key = f'location_{index}_image_existing'

            # Check if the location exists
            if desc_key not in texts:
                break  # No more locations

            # Extract texts for the location
            description = texts.get(desc_key, '')
            events = texts.get(events_key, '')
            npcs = texts.get(npcs_key, '')
            investigation = texts.get(investigation_key, '')

            # Combine all texts into one string
            combined_text = f"Description:\n{description}\n\nEvents:\n{events}\n\nNPCs:\n{npcs}\n\nInvestigation Findings:\n{investigation}"

            # Get image filename
            image_filename = images.get(img_key)

            if not image_filename:
                # Try to get existing image
                image_filename = texts.get(img_existing_key, '')

            image_path = os.path.join('static', 'uploads', secure_filename(image_filename)) if image_filename else None

            locations.append({
                'combined_text': combined_text,
                'image_path': image_path
            })
            index += 1

        for location in locations:
            # Front side (image)
            pdf.add_page()
            if location['image_path'] and os.path.exists(secure_filename(location['image_path'])):
                pdf.image(secure_filename(location['image_path']), x=0, y=0, w=148, h=210)
            else:
                pdf.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
                pdf.set_font('DejaVu', '', 16)
                pdf.set_xy(0, 100)
                pdf.cell(0, 10, 'Image Not Available', ln=True, align='C')

            # Back side (text)
            pdf.add_page()
            pdf.set_auto_page_break(auto=False)
            pdf.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
            max_font_size = 12  # Start with maximum font size
            min_font_size = 6  # Minimum font size to attempt
            x0, y0 = 10, 10
            available_width = 128  # 148 mm width - margins (10 mm on each side)
            available_height = 210 - y0 - 10  # Page height - top margin - bottom margin
            font_size = max_font_size

            # Try to find the largest font size where the text fits
            while font_size >= min_font_size:
                pdf.set_font('DejaVu', '', font_size)
                line_height = pdf.font_size * 1.2  # Approximate line height
                text_lines = split_text_to_fit(pdf, location['combined_text'], available_width)
                total_height = line_height * len(text_lines)

                if total_height <= available_height:
                    break  # Text fits
                else:
                    font_size -= 1  # Decrease font size and try again

            # Set the font to the determined size and write the text
            pdf.set_font('DejaVu', '', font_size)
            pdf.set_xy(x0, y0)
            pdf.multi_cell(available_width, line_height, location['combined_text'], align='C')

        pdf.output(output_path)

    def generate_additional_text_pdf(self, output_path):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
        pdf.set_font('DejaVu', '', 16)
        # pdf.set_font("Arial", size=12)

        texts = self.board_game_data.get('texts', {})
        for key, value in texts.items():
            if key not in ['npcs', 'locations']:
                pdf.multi_cell(0, 10, f"{key.replace('_', ' ').title()}:\n{value}\n\n")

        pdf.output(output_path)

def split_text_to_fit(pdf, text, cell_width):
    import re

    # Helper function to split text into lines that fit within cell_width
    words = re.split(r'(\s+)', text)
    lines = []
    line = ''
    for word in words:
        if word.strip() == '':
            # Spaces or empty strings, add them to the line
            line += word
            continue

        # Try adding the word to the current line
        test_line = line + word
        line_width = pdf.get_string_width(test_line)
        if line_width <= cell_width:
            line = test_line
        else:
            # Line is too long, add the current line to lines and start a new line
            lines.append(line)
            line = word

    if line:
        lines.append(line)

    # Split lines at newlines
    final_lines = []
    for l in lines:
        final_lines.extend(l.split('\n'))

    return final_lines

class Unit(db.Model):
    __tablename__ = 'unit'
    id = db.Column(db.Integer, primary_key=True)
    unit_type = db.Column(db.String(50))
    story_id = db.Column(db.Integer, db.ForeignKey('story.id'), nullable=False)
    features = db.Column(MutableDict.as_mutable(db.JSON))  # For mutable JSON support

    base_feature_schema = {'name': str}  # Add this to fix the AttributeError

    __mapper_args__ = {
        'polymorphic_identity': 'unit',
        'polymorphic_on': unit_type
    }

    @property
    def name(self):
        return self.features.get('name', '').strip()

    @name.setter
    def name(self, value):
        self.features['name'] = value.strip()

    @validates('features')
    def validate_features(self, key, value):
        if 'name' in value:
            value['name'] = value['name'].strip()
        return value

    def to_json(self):
        '''
        Serialize the unit into a JSON-friendly dictionary.
        '''
        unit_dict = {
            'id': self.id,
            'unit_type': self.unit_type,
            'features': self.features
        }
        return unit_dict

# Subclasses of Unit

# Subclasses of Unit

class EventOrScene(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Which people are involved?': list,
        'Which groups are involved?': list,
        'Which beasts are involved?': list,
        'Which items are involved?': list,
        'Which secrets are involved?': list,
        'What motivations are involved?': list,
        'Where might this happen?': list,
        'Is this an investigation scene?': bool,
        'Is this a social interaction?': bool,
        'Is this a fight scene?': bool,
        'What happens?': str,
        'How do relationships change?': str,
        'What triggers this scene to happen?': str,
        'Is this scene a start scene?': bool,
        "If this scene is a start scene, who's start scene is it?": list,
        'How likely will this scene occur?': float,
    }}

    __mapper_args__ = {
        'polymorphic_identity': 'EventOrScene',
    }


class Secret(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'What is the secret?': str,
        'Who knows of it?': list,
        'Which people are involved?': list,
        'Which groups are involved?': list,
        'Which items are involved?': list,
        'On a scale of 0.0 to 1.0 how exciting is it to learn about this secret?': float
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'Secret',
    }


class Item(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Who owns this?': list,
        'On a scale of 0.0 to 1.0 where 0 is just very little and 1 is very much; how much is it worth?': float,
        'What is it?': str,
        'Where is it?': list
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'Item',
    }


class Beast(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Which race is this beast?': str,
        'Where could it be?': list,
        'What does it look like?': str,
        'On a scale of 0.0 (not aggressive) to 1.0 (immediate attack), how aggressive is the beast?': float
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'Beast',
    }


class Group(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Who is part of the group?': list,
        'What makes them a group / What is the reason for solidarity?': str,
        'Where did the group first meet?': list
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'Group',
    }


class Motivation(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Who is motivated?': list,
        'What is the motivation for?': str,
        'By whom is the motivation?': list,
        'Is ambition the source of motivation?': bool,
        'Is determination the source of motivation?': bool,
        'Is passion the source of motivation?': bool,
        'Is enthusiasm the source of motivation?': bool,
        'Is curiosity the source of motivation?': bool,
        'Is confidence the source of motivation?': bool,
        'Is optimism the source of motivation?': bool,
        'Is perseverance the source of motivation?': bool,
        'Is joyful challenge the source of motivation?': bool,
        'Is growth the source of motivation?': bool,
        'Is fear the source of motivation?': bool,
        'Is frustration the source of motivation?': bool,
        'Is anger the source of motivation?': bool,
        'Is discontent the source of motivation?': bool,
        'Is disappointment the source of motivation?': bool,
        'Is dissatisfaction the source of motivation?': bool,
        'Is regret the source of motivation?': bool,
        'Is avoidance the source of motivation?': bool,
        'Is restlessness the source of motivation?': bool,
        'Is desperation the source of motivation?': bool,
        'Is caution the source of motivation?': bool,
        'Is reflection the source of motivation?': bool
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'Motivation',
    }


class Place(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Where is it?': str,
        'What are the environmental conditions?': str,
        'What other places is this place associated with?': list,
        'What people are there?': list,
        'What groups are there?': list,
        'What beasts are there?': list,
        'What items are there?': list,
        'What secrets can be found here?': list,
        'What size is it? On a scale of 0.0 to 1.0 where 0 is a very small cabin and 1 is a big city.': float,
        'What does it look like?': str,
        'What is the special history of this place?': list,
        'What will happen at or with this place?': str,
        'Is it a space in nature?': bool,
        'Is it an urban space?': bool,
        'Is it a desert?': bool,
        'Is it a forest?': bool,
        'Is it a mountain range?': bool,
        'Is it a body of water?': bool,
        'Is it a coastline?': bool,
        'Is it an island?': bool,
        'Is it a grassland?': bool,
        'Is it a park?': bool,
        'Is it a cave?': bool
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'Place',
    }


class TransportationInfrastructure(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Which places does it connect?': list,
        'How frequent does this route get taken? On a scale of 0.0 (barely ever) to 1.0 (constantly).': float,
        'Is this transportation infrastructure for motor vehicles?': bool,
        'Is this transportation infrastructure for non-motor vehicles?': bool,
        'Is this transportation infrastructure for pedestrians?': bool,
        'Is it a street?': bool,
        'Is it a railway?': bool,
        'Is it a flying route?': bool,
        'Is it a boat route?': bool,
        'Is it a tunnel?': bool,
        'Is it a bridge?': bool
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'TransportationInfrastructure',
    }


class Character(Unit):
    feature_schema = {**Unit.base_feature_schema, **{
        'Is this a player character?': bool,
        'What is this Character skilled or talented at?': str,
        'Which Events or Scenes involve this Character?': list,
        'Which groups is this Character a part of?': list,
        'What are plans of or with this Character?': list,
        'What\'s this Characters backstory?': str,
        'Who is important for this Character?': list,
        'What is important for this Character?': list,
    }}
    __mapper_args__ = {
        'polymorphic_identity': 'Character',
    }
